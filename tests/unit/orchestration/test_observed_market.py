from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from app.domain.market.freshness import DataFreshness
from app.domain.market.models import Quote
from app.orchestration.observed_market import build_observed_market
from app.persistence.jsonl_file import JsonlQuoteRepository

_NIFTY_KEY = "NSE_INDEX|Nifty 50"
_MASTER = [
    {
        "segment": "NSE_INDEX",
        "name": "Nifty 50",
        "exchange": "NSE",
        "instrument_type": "INDEX",
        "instrument_key": _NIFTY_KEY,
        "trading_symbol": "NIFTY",
    },
]


def _quote(ts: datetime, *, price: str, previous: str) -> Quote:
    return Quote(
        provider="upstox",
        freshness=DataFreshness(data_timestamp=ts, received_timestamp=ts),
        instrument_id=_NIFTY_KEY,
        last_price=Decimal(price),
        previous_close=Decimal(previous),
    )


def test_observed_market_is_unavailable_without_a_quote(tmp_path: Path) -> None:
    repo = JsonlQuoteRepository(tmp_path / "quotes.jsonl")
    payload = asyncio.run(build_observed_market(
        instrument_master=_MASTER, quotes=repo, as_of=datetime(2026, 9, 18, 12, 0, tzinfo=UTC),
    ))
    assert payload["indices"]["nifty"]["observation_kind"] == "UNAVAILABLE"
    assert payload["indices"]["nifty"]["last_price"] is None
    assert payload["indices"]["vix"]["observation_kind"] == "UNAVAILABLE"


def test_observed_market_returns_persisted_print_as_last_observed_when_closed(tmp_path: Path) -> None:
    repo = JsonlQuoteRepository(tmp_path / "quotes.jsonl")
    ts = datetime(2026, 9, 18, 10, 28, tzinfo=UTC)  # 15:58 IST
    asyncio.run(repo.save(_quote(ts, price="25100.25", previous="25200.00")))
    # Saturday 19 Sep 2026 12:00 UTC is a closed session.
    payload = asyncio.run(build_observed_market(
        instrument_master=_MASTER, quotes=repo, as_of=datetime(2026, 9, 19, 12, 0, tzinfo=UTC),
    ))
    nifty = payload["indices"]["nifty"]
    assert nifty["last_price"] == "25100.25"
    assert nifty["observation_kind"] == "LAST_OBSERVED"
    assert nifty["day_change_pct"] is not None
    assert "15:58 IST" in (nifty["observed_at_ist"] or "")
    assert payload["indices"]["banknifty"]["observation_kind"] == "UNAVAILABLE"
    assert payload["breadth"]["observation_kind"] == "UNAVAILABLE"
    assert payload["indices"]["nifty"]["freshness_label"] == "MARKET_CLOSED"


def test_observed_market_never_invents_an_index_without_a_master_match(tmp_path: Path) -> None:
    repo = JsonlQuoteRepository(tmp_path / "quotes.jsonl")
    payload = asyncio.run(build_observed_market(
        instrument_master=[], quotes=repo, as_of=datetime(2026, 9, 18, 12, 0, tzinfo=UTC),
    ))
    assert payload["indices"]["nifty"]["unavailable_reason"] == "instrument not in loaded master"


def test_older_index_print_is_labeled_stale_relative_to_the_newest_observed_session(tmp_path: Path) -> None:
    bank_key = "NSE_INDEX|Nifty Bank"
    master = [
        *_MASTER,
        {
            "segment": "NSE_INDEX",
            "name": "Nifty Bank",
            "exchange": "NSE",
            "instrument_type": "INDEX",
            "instrument_key": bank_key,
            "trading_symbol": "BANKNIFTY",
        },
    ]
    repo = JsonlQuoteRepository(tmp_path / "quotes.jsonl")
    newer = datetime(2026, 9, 18, 10, 30, tzinfo=UTC)
    older = datetime(2026, 9, 9, 10, 0, tzinfo=UTC)
    asyncio.run(repo.save(_quote(newer, price="25100.25", previous="25200.00")))
    asyncio.run(repo.save(Quote(
        provider="upstox",
        freshness=DataFreshness(data_timestamp=older, received_timestamp=older),
        instrument_id=bank_key,
        last_price=Decimal("56000"),
        previous_close=Decimal("56500"),
    )))
    payload = asyncio.run(build_observed_market(
        instrument_master=master, quotes=repo, as_of=datetime(2026, 9, 19, 12, 0, tzinfo=UTC),
    ))
    assert payload["indices"]["nifty"]["freshness_label"] == "MARKET_CLOSED"
    assert payload["indices"]["banknifty"]["freshness_label"] == "STALE"
    assert payload["indices"]["nifty"]["observed_at_ist"] != payload["indices"]["banknifty"]["observed_at_ist"]
