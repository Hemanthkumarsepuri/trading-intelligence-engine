# Live WebSocket Streaming — Status

Status: **Not implemented. Architecture ready to receive it.**

## Why it wasn't built this milestone

Upstox's market-data-feed WebSocket (confirmed against current docs, 2026-08-28) uses **protobuf binary encoding**, not JSON — "the WebSocket request message should be sent in binary format... incoming data is structured as protobuf messages requiring language-specific compilation" against Upstox's own `MarketDataFeed.proto` schema file. Building a wire-protocol client against a binary schema that:
1. hasn't been fetched/reviewed in this session, and
2. cannot be exercised against a real connection without a live `UPSTOX_ACCESS_TOKEN`,

would produce untested, unverifiable code — a worse outcome than not building it. The connection flow itself also involves an "authorize then redirect to the authorized endpoint" step whose exact mechanics (a separate REST call returning a WS URL, per the docs' phrasing) were not confirmed in enough detail to implement confidently.

## What IS ready to receive a live feed once built

Everything downstream of "a decoded tick arrives" already exists and is fully tested:
- `app/domain/market/live_candle_aggregator.py` — `LiveCandleAggregator.on_tick(price, volume_since_last_tick, timestamp)` turns a tick stream into completed M15 candles + one in-progress `PartialCandle`, with no-look-ahead and out-of-order rejection already proven (`tests/unit/market/test_live_candle_aggregator.py`).
- `app/domain/strategy/current_analysis.py` — `assemble_current_analysis()` turns `(MarketState, completed_candles, current_partial_candle, strategy, as_of)` into one auditable result, already tested against both direct and live-tick-reconstructed candles (`tests/scripts/test_live_analysis_demo.py::test_final_result_matches_direct_batch_computation`).
- `scripts/live_analysis_demo.py` proves this exact pipeline end-to-end today, with a synthetic tick source standing in for the real feed.

## What building the real WebSocket client requires

1. `UPSTOX_ACCESS_TOKEN` (see `docs/data-sources/PROVIDER_DECISION.md`) — needed to test the connection at all.
2. Fetching and reviewing Upstox's `MarketDataFeed.proto` schema (or a pre-compiled Python binding, if Upstox publishes one) to decode incoming binary messages.
3. Pin a protobuf runtime compatible with the checked-in gencode (`protobuf>=7.35.1` in `pyproject.toml`, matching `app/data/providers/upstox_market_data_feed_pb2.py` which is generated with Protobuf Python 7.35.1).
4. A thin adapter that: makes the REST "authorize" call, connects the WS with the returned URL, decodes each binary message into a `(price, volume, timestamp)` tick, and calls `LiveCandleAggregator.on_tick()` — exactly the shape `scripts/live_analysis_demo.py`'s synthetic tick loop already exercises.

None of step 4 needs architecture changes — it is a new, thin, dependency-injectable adapter (mirroring `DhanProvider`/`UpstoxProvider`'s own `httpx.AsyncClient`-injection pattern, so its message-decoding logic can still be unit-tested against fake binary payloads without a live connection) once steps 1-3 are in place.
