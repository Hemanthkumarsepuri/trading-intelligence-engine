"""Protobuf gencode/runtime compatibility (Case J).

Checked-in gencode is Protobuf Python 7.35.1
(`app/data/providers/upstox_market_data_feed_pb2.py`). The runtime must
be new enough to import it -- `pyproject.toml` pins `protobuf>=7.35.1`.
"""

from __future__ import annotations


def test_generated_feed_protobuf_imports() -> None:
    from app.data.providers.upstox_market_data_feed_pb2 import FeedResponse

    assert FeedResponse is not None


def test_feed_decoder_imports() -> None:
    from app.data.providers.upstox_feed_decoder import decode_feed_message

    assert callable(decode_feed_message)
