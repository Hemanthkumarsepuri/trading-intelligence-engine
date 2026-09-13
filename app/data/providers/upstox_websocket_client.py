"""Upstox Market Data Feed V3 — WebSocket client.

Connection flow (confirmed against Upstox's official docs, 2026-08-28 —
`upstox.com/developer/api-documentation/{v3/get-market-data-feed,
get-market-data-feed-authorize}/`):

1. `GET /v3/feed/market-data-feed/authorize` with `Authorization: Bearer
   {access_token}` returns a single-use, short-lived authorized WebSocket
   URL (`data.authorized_redirect_uri`; the camelCase
   `authorizedRedirectUri` variant is documented as deprecated).
2. Connect to that `wss://` URL.
3. Send a subscription message as a **binary** frame containing UTF-8 JSON
   (`{"guid", "method": "sub", "data": {"mode", "instrumentKeys"}}`) — see
   `upstox_feed_decoder.build_subscribe_message()`.
4. Receive binary protobuf `FeedResponse` messages — see
   `upstox_feed_decoder.decode_feed_message()`.
5. Upstox sends periodic `ping` frames when there's no data to stream; the
   `websockets` library answers `pong` automatically, so nothing app-level
   is needed for that specific mechanism. This client's own
   `recv_timeout_seconds` is a separate, coarser staleness signal — "no
   *decodable message* arrived recently" — for the caller to act on.

This client only ever calls read-only market-data endpoints. No order,
account, or portfolio endpoint is referenced anywhere in this module.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, Protocol

import httpx
import websockets
from websockets.exceptions import ConnectionClosed as _WebsocketsConnectionClosed

from app.data.providers.base import RawTick
from app.data.providers.exceptions import ProviderMalformedResponse, ProviderUnavailable
from app.data.providers.upstox_feed_decoder import build_subscribe_message, decode_feed_message


class FeedConnectionClosed(Exception):
    """Raised by a `WebSocketConnection.recv()` implementation when the
    underlying connection has closed. Deliberately not tied to any specific
    WebSocket library's exception type, so `UpstoxLiveFeedClient.stream()`'s
    reconnect logic can be tested against a fake connection without
    depending on `websockets` internals.
    """


class WebSocketConnection(Protocol):
    """The minimal shape `UpstoxLiveFeedClient` needs from a live
    connection — small enough to fake completely in tests.
    """

    async def recv(self) -> bytes: ...
    async def send(self, message: bytes) -> None: ...
    async def close(self) -> None: ...


class _WebsocketsAdapter:
    """Wraps a real `websockets` connection to satisfy `WebSocketConnection`,
    translating its connection-closed exception into `FeedConnectionClosed`.
    """

    def __init__(self, connection: object) -> None:
        self._connection = connection

    async def recv(self) -> bytes:
        try:
            message = await self._connection.recv()  # type: ignore[attr-defined]
        except _WebsocketsConnectionClosed as exc:
            raise FeedConnectionClosed(str(exc)) from exc
        return message if isinstance(message, bytes) else str(message).encode("utf-8")

    async def send(self, message: bytes) -> None:
        await self._connection.send(message)  # type: ignore[attr-defined]

    async def close(self) -> None:
        await self._connection.close()  # type: ignore[attr-defined]


async def _default_connect(ws_url: str) -> WebSocketConnection:
    connection = await websockets.connect(ws_url)
    return _WebsocketsAdapter(connection)


FeedEventKind = Literal["connected", "tick", "stale", "malformed", "disconnected", "reconnecting", "stopped"]


@dataclass
class FeedEvent:
    """One observable moment in the live feed's lifecycle. `stream()` yields
    these instead of taking an `on_tick` callback so reconnect/stale/
    lifecycle behavior is visible and testable the same way ticks are,
    rather than being buried in side effects.
    """

    kind: FeedEventKind
    tick: RawTick | None = None
    detail: str | None = None
    attempt: int | None = None


class UpstoxLiveFeedClient:
    """Read-only. No method here calls, references, or is capable of
    calling any order/account/trading endpoint.
    """

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        access_token: str,
        base_url: str = "https://api.upstox.com",
        connect: Callable[[str], Awaitable[WebSocketConnection]] = _default_connect,
    ) -> None:
        self._client = client
        self._access_token = access_token
        self._base_url = base_url.rstrip("/")
        self._connect = connect

    async def authorize(self) -> str:
        """Returns the authorized, single-use `wss://` URL. Never logs or
        returns this alongside the access token — the caller is responsible
        for not printing/logging the returned URL either, since it embeds a
        single-use auth code.
        """
        try:
            response = await self._client.get(
                f"{self._base_url}/v3/feed/market-data-feed/authorize",
                headers={"Authorization": f"Bearer {self._access_token}", "Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"Upstox feed authorize request failed: {exc}") from exc

        if response.status_code >= 400:
            raise ProviderMalformedResponse(
                f"Upstox feed authorize returned HTTP {response.status_code}: {response.text[:200]}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderMalformedResponse("Upstox feed authorize returned a non-JSON body") from exc

        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise ProviderMalformedResponse("Upstox feed authorize response missing 'data'")
        ws_url = data.get("authorized_redirect_uri") or data.get("authorizedRedirectUri")
        if not isinstance(ws_url, str) or not ws_url:
            raise ProviderMalformedResponse("Upstox feed authorize response missing an authorized redirect URL")
        return ws_url

    async def stream(
        self,
        instrument_keys: list[str],
        *,
        mode: str = "ltpc",
        max_reconnects: int = 5,
        reconnect_delay_seconds: float = 2.0,
        recv_timeout_seconds: float = 30.0,
    ) -> AsyncGenerator[FeedEvent, None]:
        """Connects, subscribes, and yields `FeedEvent`s until either the
        caller stops iterating (the connection is closed in a `finally`) or
        `max_reconnects` consecutive reconnect attempts are exhausted.
        """
        attempt = 0
        while True:
            connection: WebSocketConnection | None = None
            try:
                ws_url = await self.authorize()
                connection = await self._connect(ws_url)
                await connection.send(build_subscribe_message(instrument_keys, mode=mode))
                yield FeedEvent(kind="connected")

                while True:
                    try:
                        raw = await asyncio.wait_for(connection.recv(), timeout=recv_timeout_seconds)
                    except TimeoutError:
                        yield FeedEvent(kind="stale", detail=f"no message received for {recv_timeout_seconds}s")
                        continue

                    # Reset the backoff counter only once a message has
                    # actually been received successfully -- resetting it on
                    # a bare TCP-level connect would let a server that
                    # accepts a connection and then immediately drops it
                    # (no error, no data) defeat `max_reconnects` entirely,
                    # looping forever instead of ever reaching "stopped".
                    attempt = 0

                    try:
                        ticks = decode_feed_message(raw)
                    except ProviderMalformedResponse as exc:
                        # One malformed message must never take down an
                        # otherwise-healthy connection -- report and move on.
                        yield FeedEvent(kind="malformed", detail=str(exc))
                        continue
                    for tick in ticks:
                        yield FeedEvent(kind="tick", tick=tick)

            except FeedConnectionClosed as exc:
                attempt += 1
                yield FeedEvent(kind="disconnected", detail=str(exc))
                if attempt > max_reconnects:
                    yield FeedEvent(kind="stopped", detail=f"exceeded {max_reconnects} reconnect attempts")
                    return
                yield FeedEvent(kind="reconnecting", attempt=attempt)
                await asyncio.sleep(reconnect_delay_seconds)
                continue
            finally:
                if connection is not None:
                    await connection.close()
