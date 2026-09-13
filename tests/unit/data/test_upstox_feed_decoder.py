"""Tests for the Upstox live-feed protobuf decoder. Builds real
`FeedResponse` protobuf messages using the actual generated classes
(`upstox_market_data_feed_pb2`, compiled from Upstox's own published
schema) rather than hand-crafted byte sequences — this exercises the real
wire format, not a guess at it.
"""

from __future__ import annotations

import json

import pytest

from app.data.providers import upstox_market_data_feed_pb2 as pb
from app.data.providers.exceptions import ProviderMalformedResponse
from app.data.providers.upstox_feed_decoder import (
    build_subscribe_message,
    build_unsubscribe_message,
    decode_feed_message,
)

INSTRUMENT_KEY = "NSE_EQ|INE002A01018"


def test_build_subscribe_message_shape() -> None:
    message = build_subscribe_message([INSTRUMENT_KEY], mode="ltpc", guid="g1")
    payload = json.loads(message.decode("utf-8"))

    assert payload == {"guid": "g1", "method": "sub", "data": {"mode": "ltpc", "instrumentKeys": [INSTRUMENT_KEY]}}


def test_build_subscribe_message_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="mode"):
        build_subscribe_message([INSTRUMENT_KEY], mode="not_a_real_mode")


def test_build_unsubscribe_message_shape() -> None:
    message = build_unsubscribe_message([INSTRUMENT_KEY], guid="g2")
    payload = json.loads(message.decode("utf-8"))

    assert payload == {"guid": "g2", "method": "unsub", "data": {"instrumentKeys": [INSTRUMENT_KEY]}}


def _feed_response_bytes(*, ltpc_mode: bool) -> bytes:
    response = pb.FeedResponse(type=pb.Type.live_feed, currentTs=1756290000000)
    if ltpc_mode:
        response.feeds[INSTRUMENT_KEY].ltpc.CopyFrom(pb.LTPC(ltp=1290.5, ltt=1756290000000, ltq=50, cp=1285.0))
    else:
        full = pb.FullFeed(marketFF=pb.MarketFullFeed(ltpc=pb.LTPC(ltp=1291.0, ltt=1756290000500, ltq=25, cp=1285.0)))
        response.feeds[INSTRUMENT_KEY].fullFeed.CopyFrom(full)
    return bytes(response.SerializeToString())


def test_decode_ltpc_mode_message() -> None:
    ticks = decode_feed_message(_feed_response_bytes(ltpc_mode=True))

    assert len(ticks) == 1
    tick = ticks[0]
    assert tick.instrument_key == INSTRUMENT_KEY
    assert tick.price == 1290.5
    assert tick.quantity == 50
    assert tick.timestamp.isoformat() == "2025-08-27T10:20:00+00:00"


def test_decode_ltpc_mode_message_is_not_flagged_as_initial_snapshot() -> None:
    ticks = decode_feed_message(_feed_response_bytes(ltpc_mode=True))
    assert ticks[0].is_initial_snapshot is False


def test_decode_initial_feed_message_flags_ticks_as_initial_snapshot() -> None:
    response = pb.FeedResponse(type=pb.Type.initial_feed)
    response.feeds[INSTRUMENT_KEY].ltpc.CopyFrom(pb.LTPC(ltp=1290.5, ltt=1756290000000, ltq=50))

    ticks = decode_feed_message(response.SerializeToString())

    assert len(ticks) == 1
    assert ticks[0].is_initial_snapshot is True


def test_decode_full_mode_message_extracts_nested_ltpc() -> None:
    ticks = decode_feed_message(_feed_response_bytes(ltpc_mode=False))

    assert len(ticks) == 1
    assert ticks[0].price == 1291.0
    assert ticks[0].quantity == 25


def test_decode_message_with_no_ltpc_payload_yields_no_ticks() -> None:
    response = pb.FeedResponse(type=pb.Type.market_info)
    response.marketInfo.segmentStatus["NSE_EQ"] = pb.MarketStatus.NORMAL_OPEN

    ticks = decode_feed_message(response.SerializeToString())

    assert ticks == []


def test_decode_multiple_instruments_in_one_message() -> None:
    response = pb.FeedResponse(type=pb.Type.live_feed)
    response.feeds["A"].ltpc.CopyFrom(pb.LTPC(ltp=100.0, ltt=1756290000000, ltq=1))
    response.feeds["B"].ltpc.CopyFrom(pb.LTPC(ltp=200.0, ltt=1756290000000, ltq=2))

    ticks = decode_feed_message(response.SerializeToString())

    assert {t.instrument_key for t in ticks} == {"A", "B"}


def test_decode_garbage_bytes_raises_malformed_response() -> None:
    with pytest.raises(ProviderMalformedResponse):
        decode_feed_message(b"\xff\xff\xff not a valid protobuf message at all \x00\x01")


def test_decode_rejects_non_positive_ltt() -> None:
    response = pb.FeedResponse(type=pb.Type.live_feed)
    response.feeds[INSTRUMENT_KEY].ltpc.CopyFrom(pb.LTPC(ltp=100.0, ltt=0, ltq=1))

    with pytest.raises(ProviderMalformedResponse, match="ltt"):
        decode_feed_message(response.SerializeToString())
