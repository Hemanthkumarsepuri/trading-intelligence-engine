# Real-Time Market Intelligence — Architecture

Status: **Implemented and validated against real data, 2026-08-28.** Records what exists, not a speculative roadmap.

## Provider Architecture

**Upstox is the sole active provider**, confirmed sufficient for everything this layer needs (see NSE investigation below). Dhan remains implemented (`app/data/providers/dhan_provider.py`), unused, ready if a genuine gap ever appears. Nothing here forecloses a second provider — `MarketDataProvider` is still the shared Protocol — but none is wired in because none is needed yet.

## NSE Direct-Connectivity Investigation — Result: **Do not integrate NSE directly**

Verified against NSE's own published data-licensing material (`nseindia.com/national-stock-exchange/data-products-services`, `nseindia.com/static/market-data/real-time-data-subscription`) and third-party authorized-vendor pages, 2026-08-28:

- NSE's real-time feed is delivered over **multicast via a dedicated leased line** from an NSE point-of-presence to the customer's own premises — physical infrastructure, categorically unavailable to a personal application.
- Any data resale/feed use requires **written consent and a prior license/agreement with NSE Data & Analytics Ltd** — an institutional licensing process, not self-serve developer signup.
- Multiple "NSE-authorized data vendors" (TrueData, Global Datafeeds, etc.) exist specifically because direct retail access isn't practical — they carry the licensing burden and resell access, the same role Upstox already plays for us.
- **Upstox already sources its own data via exactly this kind of licensed NSE relationship** — we already benefit from real NSE data through Upstox without needing a separate license.

Breadth/advances-declines, FII/DII flows, delivery %, and bulk/block deals are NSE **reporting/analytics** products (not part of the real-time trade feed either). This phase adds **SAMPLE-UNIVERSE BREADTH** (official Nifty 50 constituent CSV on `nsearchives.nseindia.com` plus authorized Upstox quotes — not NSE official A/D), **previous-session EOD delivery** from `sec_bhavdata_full_{ddmmyyyy}.csv`, and **FII/DII as UNKNOWN or caller-supplied** (legacy `.xls` is not parsed; no interactive nseindia.com scrape). None of these is a directional EvidenceGroup vote.

**Second-provider decision:** not needed right now. Upstox already covers LTP, quotes, market status, historical M15, and live WebSocket ticks — everything this milestone's intelligence layer consumes.

Official CM-segment holidays and Muhurat (`SPECIAL:`) dates are transcribed from NSE circular NSE/CMTR/71775 into `data/reference/nse_trading_holidays.txt` and applied at API startup. The in-module `NSE_HOLIDAYS` constant stays empty.

## Data Flow

```
Upstox instrument master (real, cached — app/data/providers/upstox_instrument_master.py)
        |
        v
resolve_symbol() -> instrument_key            (never hardcoded)
        |
        v
UpstoxProvider: get_market_status() / get_quotes() (batched) / get_ohlcv()
        |
        v
DefaultNormalizer -> Quote / Candle             (unchanged, reused)
        |
        v
app/orchestration/watchlist_snapshot.py: build_watchlist_snapshot()
        |    - classify_market_data_state() (app/domain/market/data_state.py)
        |    - assemble_current_analysis()   (unchanged, reused — EMA/VWAP/strategy)
        v
InstrumentSnapshot  (one per instrument)
        |
        v
app/orchestration/analysis_change.py: detect_changes(previous, current)
        |
        v
scripts/market_intelligence_demo.py: MARKET SNAPSHOT / ATTENTION REQUIRED report
```

For the live-tick path (market hours), the same `InstrumentSnapshot`/`assemble_current_analysis()` stages are fed by:
```
UpstoxLiveFeedClient.stream()  (unchanged, reused)
        |
        v
app/domain/market/multi_instrument_aggregator.py: MultiInstrumentCandleAggregator
        |    (routes each tick to a per-instrument LiveCandleAggregator)
        v
completed/partial M15 candle, per instrument
```

## Freshness Model (unchanged, reused)

`DataFreshness.data_timestamp` — the real exchange timestamp (`last_trade_time` from `/v2/market-quote/quotes`, confirmed live) — never the local receipt instant when the provider supplies a real one. `MarketDataState` (`app/domain/market/data_state.py`) turns `(exchange_status, is_live_stream, data_age, candles_available)` into one of seven explicit states — precedence: insufficient history > market closed > stale > live streaming > live snapshot. A closed market with a fresh-looking quote is always `MARKET_CLOSED_LATEST_DATA`, never `LIVE_*`.

## Market-State Model

No new "market state" type was built — `InstrumentSnapshot` (quote + market status + freshness) and `CurrentAnalysisResult` (EMA9/21/50, alignment, VWAP, price-vs-VWAP, setup), both already existing, jointly cover every field this milestone needed. Every field traces to a real producer:

| Field | Source | Producer |
|---|---|---|
| LTP, previous close, day change, volume, exchange timestamp | `/v2/market-quote/quotes` | `UpstoxProvider.get_quotes()` |
| Market status | `/v2/market/status/NSE` | `UpstoxProvider.get_market_status()` |
| M15 candles | `/v3/historical-candle/...` | `UpstoxProvider.get_ohlcv()` (unchanged) |
| EMA9/21/50, alignment, VWAP, price-vs-VWAP, setup | computed from the candles above | `assemble_current_analysis()` (unchanged) |
| Data-quality classification | derived from the above | `classify_market_data_state()` |

Market-context fields with no verified producer (breadth, sector strength, OI change, futures basis, option-chain context) were **not added** — India VIX and NIFTY/BANKNIFTY are already representable as ordinary watchlist instruments (their own EMA/VWAP facts), which is as far as real evidence currently supports.

## Watchlist

Configurable, symbol-based, resolved programmatically — `DEFAULT_WATCHLIST` in `scripts/market_intelligence_demo.py`: NIFTY50, BANKNIFTY, RELIANCE, TCS, INFY, HDFCBANK, ICICIBANK, SBIN. Any symbol present in Upstox's real instrument master works; an unresolvable symbol produces an explicit `ERROR` state, never a guess.

## Event/Change Model

`detect_changes()` (`app/orchestration/analysis_change.py`) compares two `InstrumentSnapshot`s and emits only named, structural transitions — market status changed, data state changed, EMA alignment changed, price-vs-VWAP relation changed, setup appeared/disappeared/changed direction, a new candle completed. A bare number moving (LTP, an EMA's own value, VWAP) is deliberately never reported — this keeps "what changed" from degrading into noise.

## Evidence Structure

Already satisfied by existing fields, not rebuilt: `Setup.structural_basis` plus `CurrentAnalysisResult`'s own EMA-alignment/price-vs-VWAP fields together are the auditable evidence chain (`EMA alignment = X` → `price vs VWAP = Y` → `setup = Z`). No confidence score exists or was added — none has a mathematically defined basis yet.

## Failure Behavior

Fails closed at every stage: an unresolvable symbol, a failed quote/status/history call, or insufficient candles each produce an explicit typed state (`ERROR`/`PROVIDER_UNAVAILABLE`/`INSUFFICIENT_HISTORY`) — never a fabricated result, and one instrument's failure never blocks the rest of the watchlist (proven in `tests/unit/orchestration/test_watchlist_snapshot.py`).

## Latency (real, measured 2026-08-28)

- `get_market_status()`: ~230ms (one real sample).
- `get_quotes()` (1 instrument): min 48.5ms / avg 66.5ms / max 127.2ms (5 real samples) — network round-trip.
- `get_ohlcv()` (10-day M15 window): ~25ms (one real sample).
- Full 8-instrument watchlist (`build_watchlist_snapshot`, 1 status + 1 batched quotes + 8 historical calls): **0.45–0.46s real, measured**.
- Tick → aggregator update (application compute only, prior milestone's synthetic-pipeline measurement): avg 1.14ms / max 2.98ms over 1,280 samples.

Network/API latency and local application-compute latency are always reported separately — never conflated into a single "sub-second" claim.

## Current Limitations

- Continuous `live_feed` tick streaming and a live (market-open) strategy result still require validation during NSE trading hours (09:15–15:30 IST, Mon–Fri) — not achievable right now, honestly.
- NIFTY50/BANKNIFTY/INDIA VIX never produce a VWAP-based setup under `ema_vwap_alignment` (zero reported traded volume) — a genuine characteristic, not a defect.
- No cross-instrument market-breadth/FII-DII/sector data — no verified real-time producer exists without either an NSE license or scraping, both explicitly out of scope.
- No scoring/ranking/confidence engine yet — deliberately deferred until a mathematically defined basis exists.

## Future Extension Points (not built, not started)

A live multi-instrument WebSocket session script (`scripts/live_feed_session.py`'s multi-instrument successor, using `MultiInstrumentCandleAggregator`) is the natural next step once market-hours validation happens — architecture is ready to receive it, nothing was pre-built speculatively beyond the routing layer itself.
