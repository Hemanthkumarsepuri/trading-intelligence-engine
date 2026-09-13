"""Tests for `UpstoxLiveFeedClient` — every test injects a fake WebSocket
connection (satisfying the `WebSocketConnection` Protocol) and a mocked
`httpx` transport; no real network call is ever made.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.data.providers import upstox_market_data_feed_pb2 as pb
from app.data.providers.exceptions import ProviderMalformedResponse, ProviderUnavailable
from app.data.providers.upstox_websocket_client import (
    FeedConnectionClosed,
    UpstoxLiveFeedClient,
    WebSocketConnection,
)

INSTRUMENT_KEY = "NSE_EQ|INE002A01018"


def _client(handler: object, *, connect: object) -> UpstoxLiveFeedClient:
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]
    return UpstoxLiveFeedClient(client=http_client, access_token="tok", connect=connect)  # type: ignore[arg-type]


def _authorize_handler_ok(request: httpx.Request) -> httpx.Response:
    assert request.headers["Authorization"] == "Bearer tok"
    assert request.url.path == "/v3/feed/market-data-feed/authorize"
    return httpx.Response(200, json={"status": "success", "data": {"authorized_redirect_uri": "wss://example/feed"}})


class FakeConnection:
    """A scripted fake `WebSocketConnection`: `recv_sequence` items are
    yielded in order; an item that is an `Exception` instance is raised
    instead of returned (used to simulate a mid-stream disconnect).
    """

    def __init__(self, recv_sequence: list[bytes | Exception]) -> None:
        self._recv_sequence = list(recv_sequence)
        self.sent: list[bytes] = []
        self.closed = False

    async def recv(self) -> bytes:
        if not self._recv_sequence:
            raise FeedConnectionClosed("fake connection exhausted")
        item = self._recv_sequence.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def send(self, message: bytes) -> None:
        self.sent.append(message)

    async def close(self) -> None:
        self.closed = True


def _ltpc_message(*, price: float, ltt: int, qty: int, instrument_key: str = INSTRUMENT_KEY) -> bytes:
    response = pb.FeedResponse(type=pb.Type.live_feed)
    response.feeds[instrument_key].ltpc.CopyFrom(pb.LTPC(ltp=price, ltt=ltt, ltq=qty))
    return bytes(response.SerializeToString())


# -- authorize() --------------------------------------------------------


def test_authorize_returns_snake_case_field() -> None:
    client = _client(_authorize_handler_ok, connect=lambda url: None)

    url = asyncio.run(client.authorize())

    assert url == "wss://example/feed"


def test_authorize_falls_back_to_camel_case_field() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "success", "data": {"authorizedRedirectUri": "wss://camel/feed"}})

    client = _client(handler, connect=lambda url: None)

    url = asyncio.run(client.authorize())

    assert url == "wss://camel/feed"


def test_authorize_raises_on_missing_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "success", "data": {}})

    client = _client(handler, connect=lambda url: None)

    with pytest.raises(ProviderMalformedResponse):
        asyncio.run(client.authorize())


def test_authorize_raises_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    client = _client(handler, connect=lambda url: None)

    with pytest.raises(ProviderMalformedResponse):
        asyncio.run(client.authorize())


def test_authorize_raises_provider_unavailable_on_network_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    client = _client(handler, connect=lambda url: None)

    with pytest.raises(ProviderUnavailable):
        asyncio.run(client.authorize())


# -- stream() -------------------------------------------------------------


def test_stream_yields_connected_then_ticks() -> None:
    fake = FakeConnection([_ltpc_message(price=100.0, ltt=1756290000000, qty=1)])

    async def fake_connect(url: str) -> WebSocketConnection:
        return fake

    client = _client(_authorize_handler_ok, connect=fake_connect)

    async def collect() -> list[str]:
        kinds: list[str] = []
        agen = client.stream([INSTRUMENT_KEY])
        try:
            async for event in agen:
                kinds.append(event.kind)
                if event.kind == "disconnected":
                    break
        finally:
            await agen.aclose()
        return kinds

    kinds = asyncio.run(collect())
    assert kinds == ["connected", "tick", "disconnected"]
    assert json.loads(fake.sent[0].decode("utf-8"))["method"] == "sub"
    assert fake.closed


def test_stream_reconnects_after_disconnect_and_resubscribes() -> None:
    connections = [
        FakeConnection([_ltpc_message(price=100.0, ltt=1756290000000, qty=1)]),
        FakeConnection([_ltpc_message(price=101.0, ltt=1756290001000, qty=2)]),
    ]
    calls = {"n": 0}

    async def fake_connect(url: str) -> WebSocketConnection:
        conn = connections[calls["n"]]
        calls["n"] += 1
        return conn

    client = _client(_authorize_handler_ok, connect=fake_connect)

    async def collect() -> list[str]:
        kinds: list[str] = []
        agen = client.stream([INSTRUMENT_KEY], reconnect_delay_seconds=0.01)
        try:
            async for event in agen:
                kinds.append(event.kind)
                if kinds.count("connected") == 2 and event.kind == "tick":
                    break
        finally:
            await agen.aclose()
        return kinds

    kinds = asyncio.run(collect())
    # first connection: connected, tick, then exhausted -> disconnected -> reconnecting -> connected again -> tick
    assert kinds == ["connected", "tick", "disconnected", "reconnecting", "connected", "tick"]
    assert all(c.closed for c in connections[:1])  # first connection was cleanly closed before reconnecting


def test_stream_stops_after_exceeding_max_reconnects() -> None:
    async def fake_connect(url: str) -> WebSocketConnection:
        return FakeConnection([])  # immediately exhausted -> disconnects every time

    client = _client(_authorize_handler_ok, connect=fake_connect)

    async def collect() -> list[str]:
        return [event.kind async for event in client.stream([INSTRUMENT_KEY], max_reconnects=2, reconnect_delay_seconds=0.01)]

    kinds = asyncio.run(collect())
    assert kinds.count("disconnected") == 3  # initial + 2 retries
    assert kinds[-1] == "stopped"


def test_stream_reports_malformed_message_without_crashing() -> None:
    fake = FakeConnection(
        [b"not a valid protobuf message", _ltpc_message(price=100.0, ltt=1756290000000, qty=1)]
    )

    async def fake_connect(url: str) -> WebSocketConnection:
        return fake

    client = _client(_authorize_handler_ok, connect=fake_connect)

    async def collect() -> list[str]:
        kinds: list[str] = []
        agen = client.stream([INSTRUMENT_KEY])
        try:
            async for event in agen:
                kinds.append(event.kind)
                if event.kind == "tick":
                    break
        finally:
            await agen.aclose()
        return kinds

    kinds = asyncio.run(collect())
    assert kinds == ["connected", "malformed", "tick"]


def test_stream_reports_stale_on_recv_timeout() -> None:
    class SlowConnection(FakeConnection):
        async def recv(self) -> bytes:
            await asyncio.sleep(10)
            raise AssertionError("should have timed out before this point")

    slow = SlowConnection([])

    async def fake_connect(url: str) -> WebSocketConnection:
        return slow

    client = _client(_authorize_handler_ok, connect=fake_connect)

    async def collect() -> list[str]:
        kinds: list[str] = []
        agen = client.stream([INSTRUMENT_KEY], recv_timeout_seconds=0.02)
        try:
            async for event in agen:
                kinds.append(event.kind)
                if event.kind == "stale":
                    break
        finally:
            await agen.aclose()
        return kinds

    kinds = asyncio.run(collect())
    assert kinds == ["connected", "stale"]


def test_stream_never_sends_anything_other_than_the_subscribe_message() -> None:
    fake = FakeConnection([_ltpc_message(price=100.0, ltt=1756290000000, qty=1)])

    async def fake_connect(url: str) -> WebSocketConnection:
        return fake

    client = _client(_authorize_handler_ok, connect=fake_connect)

    async def collect() -> None:
        agen = client.stream([INSTRUMENT_KEY])
        try:
            async for event in agen:
                if event.kind == "tick":
                    break
        finally:
            await agen.aclose()

    asyncio.run(collect())
    assert len(fake.sent) == 1
    decoded = json.loads(fake.sent[0].decode("utf-8"))
    assert decoded["method"] == "sub"
    for forbidden in ("order", "place", "buy", "sell", "modify", "cancel"):
        assert forbidden not in json.dumps(decoded).lower()
