"""Real Upstox live-feed session driver — connects to the REAL WebSocket
Market Data Feed V3, subscribes to one real instrument, and reports what
actually happened. Read-only: no order/account endpoint is referenced
anywhere in this module or anything it imports.

Never logs, prints, or persists the access token or the authorized
WebSocket URL (the latter embeds a single-use auth code — treated with the
same care as the token itself).

This module distinguishes, explicitly, per the product requirement:
- market/data latency: not measured here — this process has no independent
  ground truth for when Upstox's server actually received/processed a
  trade; only Upstox could report that.
- application processing latency: measured directly — local receipt of a
  WS frame -> decoded tick -> aggregator update -> (once >=50 candles exist)
  full `CurrentAnalysisResult` assembly.
- staleness: surfaced via the feed client's own "stale" `FeedEvent` (no
  message for `recv_timeout_seconds`), never inferred from wall-clock
  guesswork inside domain code.

Run: `python -m scripts.live_feed_session [duration_seconds]` (default 20s).
Requires `UPSTOX_ACCESS_TOKEN` in the environment/`.env` — see
`docs/data-sources/PROVIDER_DECISION.md`.
"""

from __future__ import annotations

import asyncio
import sys
import time
from dataclasses import dataclass, field
from decimal import Decimal

import httpx

from app.config.settings import settings
from app.data.providers.base import RawTick
from app.data.providers.upstox_websocket_client import UpstoxLiveFeedClient
from app.domain.market.freshness import DataFreshness
from app.domain.market.live_candle_aggregator import LiveCandleAggregator
from app.domain.market.market_state import assemble_market_state
from app.domain.market.models import Quote, Timeframe
from app.domain.strategy.current_analysis import CurrentAnalysisResult, assemble_current_analysis
from app.domain.strategy.ema_vwap_alignment import EMAVWAPAlignmentStrategy

INSTRUMENT_KEY = "NSE_EQ|INE002A01018"  # RELIANCE, resolved from Upstox's own public instrument master
INSTRUMENT_ID = "RELIANCE"
DEFAULT_DURATION_SECONDS = 20.0


@dataclass
class SessionReport:
    event_counts: dict[str, int] = field(default_factory=dict)
    ticks_received: int = 0
    latest_tick: RawTick | None = None
    processing_latencies_ms: list[float] = field(default_factory=list)
    analysis_latencies_ms: list[float] = field(default_factory=list)
    last_analysis: CurrentAnalysisResult | None = None
    connect_latency_ms: float | None = None
    errors: list[str] = field(default_factory=list)


async def run_session(*, duration_seconds: float) -> SessionReport:
    if not settings.upstox_access_token:
        raise RuntimeError(
            "UPSTOX_ACCESS_TOKEN is not set -- cannot open a real session. See docs/data-sources/PROVIDER_DECISION.md."
        )

    report = SessionReport()
    aggregator = LiveCandleAggregator(instrument_id=INSTRUMENT_ID, timeframe=Timeframe.M15, provider_name="upstox-live")
    strategy = EMAVWAPAlignmentStrategy()

    async with httpx.AsyncClient() as http_client:
        client = UpstoxLiveFeedClient(client=http_client, access_token=settings.upstox_access_token, base_url=settings.upstox_base_url)

        connect_started = time.perf_counter()
        first_event_seen = False
        deadline = time.monotonic() + duration_seconds

        agen = client.stream([INSTRUMENT_KEY], mode="ltpc")
        try:
            async for event in agen:
                if not first_event_seen:
                    report.connect_latency_ms = (time.perf_counter() - connect_started) * 1000
                    first_event_seen = True

                report.event_counts[event.kind] = report.event_counts.get(event.kind, 0) + 1

                if event.kind in ("malformed", "disconnected") and event.detail:
                    report.errors.append(f"{event.kind}: {event.detail}")

                if event.kind == "tick" and event.tick is not None:
                    tick_started = time.perf_counter()
                    report.ticks_received += 1
                    report.latest_tick = event.tick

                    aggregator.on_tick(price=_decimal(event.tick.price), volume_since_last_tick=event.tick.quantity, timestamp=event.tick.timestamp)
                    report.processing_latencies_ms.append((time.perf_counter() - tick_started) * 1000)

                    requirement = strategy.required_timeframes()[0]
                    if len(aggregator.completed_candles) >= requirement.minimum_candles:
                        analysis_started = time.perf_counter()
                        as_of = event.tick.timestamp
                        quote = Quote(
                            provider="upstox-live",
                            freshness=DataFreshness(data_timestamp=as_of, received_timestamp=as_of),
                            instrument_id=INSTRUMENT_ID,
                            last_price=_decimal(event.tick.price),
                        )
                        market_state = assemble_market_state(quote, as_of=as_of)
                        report.last_analysis = assemble_current_analysis(
                            market_state=market_state,
                            completed_candles={Timeframe.M15: aggregator.completed_candles},
                            current_partial_candle=aggregator.current_partial_candle,
                            strategy=strategy,
                            as_of=as_of,
                        )
                        report.analysis_latencies_ms.append((time.perf_counter() - analysis_started) * 1000)

                if event.kind == "stopped":
                    break
                if time.monotonic() >= deadline:
                    break
        finally:
            await agen.aclose()

    return report


def _decimal(value: float) -> Decimal:
    return Decimal(str(value))


def render_report(report: SessionReport, *, duration_seconds: float) -> str:
    lines = [
        "=" * 60,
        "UPSTOX LIVE FEED SESSION - REAL CONNECTION",
        "=" * 60,
        "",
        f"Instrument: {INSTRUMENT_ID} ({INSTRUMENT_KEY})",
        f"Requested duration: {duration_seconds}s",
        f"Connect latency (authorize + WS connect + first event): "
        f"{report.connect_latency_ms:.1f} ms" if report.connect_latency_ms is not None else "Connect latency: n/a (no event observed)",
        "",
        f"Feed events observed: {report.event_counts or 'none'}",
        f"Live ticks received: {report.ticks_received}",
    ]

    if report.ticks_received == 0:
        lines += [
            "",
            "NO LIVE TRADE TICKS WERE RECEIVED.",
            "This is expected outside NSE trading hours (09:15-15:30 IST, Mon-Fri) --",
            "authentication/connection/subscription/protocol validation below is real;",
            "tick-level and current-analysis validation must be repeated during market hours.",
        ]
    else:
        latest = report.latest_tick
        assert latest is not None
        lines += [
            f"Latest tick: price={latest.price} qty={latest.quantity} exchange_ts={latest.timestamp.isoformat()}",
            "",
            f"Application processing latency (recv -> aggregator updated), n={len(report.processing_latencies_ms)}:",
        ]
        if report.processing_latencies_ms:
            samples = sorted(report.processing_latencies_ms)
            lines.append(
                f"  avg={sum(samples)/len(samples):.3f}ms  median={samples[len(samples)//2]:.3f}ms  max={samples[-1]:.3f}ms"
            )
        if report.analysis_latencies_ms:
            a = sorted(report.analysis_latencies_ms)
            lines.append(
                f"Current-analysis assembly latency, n={len(a)}: "
                f"avg={sum(a)/len(a):.3f}ms  median={a[len(a)//2]:.3f}ms  max={a[-1]:.3f}ms"
            )
        if report.last_analysis is not None:
            r = report.last_analysis
            lines += [
                "",
                "Last CurrentAnalysisResult:",
                f"  as_of={r.as_of.isoformat()}  price={r.price}  candles_available={r.candles_available}",
                f"  EMA9/21/50={r.ema9}/{r.ema21}/{r.ema50}  alignment={r.ema_alignment}",
                f"  VWAP={r.vwap_value}  price_vs_vwap={r.price_vs_vwap}",
                f"  setup={r.setup.direction if r.setup else 'NONE'}  strategy_version={r.strategy_version}",
            ]
        else:
            lines.append("(fewer than 50 completed M15 candles accumulated this session -- no strategy result yet)")

    if report.errors:
        lines += ["", "Errors/disconnects observed (safe, non-secret detail only):", *[f"  - {e}" for e in report.errors]]

    lines += ["", "=" * 60, "READ-ONLY SESSION. NO ORDER/EXECUTION ENDPOINT WAS EVER CALLED.", "=" * 60]
    return "\n".join(lines)


def main() -> int:
    duration = float(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DURATION_SECONDS
    try:
        report = asyncio.run(run_session(duration_seconds=duration))
    except RuntimeError as exc:
        print(f"WAITING: {exc}")
        return 3
    print(render_report(report, duration_seconds=duration))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
