"""Decodes Upstox Market Data Feed V3 protobuf messages into `RawTick`s, and
builds the (binary-framed JSON) subscription message the feed expects.

Schema source: `app/data/providers/upstox_market_data_feed.proto`, fetched
verbatim from Upstox's own published schema
(`https://assets.upstox.com/feed/market-data-feed/v3/MarketDataFeed.proto`,
confirmed 2026-08-28) and compiled to
`app/data/providers/upstox_market_data_feed_pb2.py` via `grpcio-tools`' protoc
(`python -m grpc_tools.protoc -I app/data/providers
--python_out=app/data/providers app/data/providers/upstox_market_data_feed.proto`
— rerun this if Upstox revises the schema; never hand-edit the generated
`_pb2.py` file).

This module only extracts `LTPC` (last price / last trade quantity / last
trade time) from each instrument's `Feed` — the minimum needed to drive
`LiveCandleAggregator`. Market depth, option Greeks, and the feed's own
native OHLC arrays are deliberately not decoded: nothing downstream consumes
them, and decoding fields with no consumer is exactly the kind of
unnecessary surface this project's "don't overbuild" discipline forbids.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from app.data.providers import upstox_market_data_feed_pb2 as pb
from app.data.providers.base import RawTick
from app.data.providers.exceptions import ProviderMalformedResponse

# Upstox's LTPC.ltt ("last trade time") is epoch milliseconds, matching the
# unit convention their REST APIs use elsewhere. Verify against a real
# decoded message before trusting this for anything beyond this adapter —
# noted explicitly rather than silently assumed.
_LTT_UNIT = "milliseconds"


def build_subscribe_message(instrument_keys: list[str], *, mode: str = "ltpc", guid: str = "tire-feed") -> bytes:
    """Builds the subscription message Upstox expects — sent as a *binary*
    WebSocket frame containing UTF-8 encoded JSON text (per the docs: "the
    WebSocket request message should be sent in binary format, not as a text
    message"). `mode` must be one of the four Upstox documents:
    `ltpc`, `option_greeks`, `full`, `full_d30`.
    """
    if mode not in ("ltpc", "option_greeks", "full", "full_d30"):
        raise ValueError(f"unsupported subscription mode {mode!r}")
    payload = {"guid": guid, "method": "sub", "data": {"mode": mode, "instrumentKeys": instrument_keys}}
    return json.dumps(payload).encode("utf-8")


def build_unsubscribe_message(instrument_keys: list[str], *, guid: str = "tire-feed") -> bytes:
    payload = {"guid": guid, "method": "unsub", "data": {"instrumentKeys": instrument_keys}}
    return json.dumps(payload).encode("utf-8")


def decode_feed_message(raw: bytes) -> list[RawTick]:
    """Decodes one binary `FeedResponse` protobuf message into zero or more
    `RawTick`s (one per subscribed instrument present in this message with
    an `ltpc` payload — an `initial_feed`/`market_info`-type message, or a
    `full`-mode message for an instrument that hasn't traded yet, may
    legitimately carry no `ltpc`, which is not an error, just nothing to
    report this message).

    Raises `ProviderMalformedResponse` if `raw` cannot be parsed as a
    `FeedResponse` at all — never returns a partially-decoded silent guess.
    """
    response = pb.FeedResponse()
    try:
        response.ParseFromString(raw)
    except Exception as exc:  # protobuf raises its own DecodeError subclasses
        raise ProviderMalformedResponse(f"could not parse FeedResponse protobuf message: {exc}") from exc

    is_initial_snapshot = response.type == pb.Type.initial_feed

    ticks: list[RawTick] = []
    for instrument_key, feed in response.feeds.items():
        ltpc = _extract_ltpc(feed)
        if ltpc is None:
            continue
        ticks.append(
            RawTick(
                instrument_key=instrument_key,
                price=ltpc.ltp,
                quantity=ltpc.ltq,
                timestamp=_ltt_to_datetime(ltpc.ltt),
                is_initial_snapshot=is_initial_snapshot,
            )
        )
    return ticks


def _extract_ltpc(feed: pb.Feed) -> pb.LTPC | None:
    which = feed.WhichOneof("FeedUnion")
    if which == "ltpc":
        return feed.ltpc
    if which == "fullFeed":
        full_which = feed.fullFeed.WhichOneof("FullFeedUnion")
        if full_which == "marketFF":
            return feed.fullFeed.marketFF.ltpc
        if full_which == "indexFF":
            return feed.fullFeed.indexFF.ltpc
        return None
    if which == "firstLevelWithGreeks":
        return feed.firstLevelWithGreeks.ltpc
    return None


def _ltt_to_datetime(ltt: int) -> datetime:
    if ltt <= 0:
        raise ProviderMalformedResponse(f"LTPC.ltt is non-positive ({ltt}); cannot derive a tick timestamp")
    return datetime.fromtimestamp(ltt / 1000, tz=UTC)
