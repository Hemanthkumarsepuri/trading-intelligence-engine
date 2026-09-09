"""Master Grooming Sprint, Phase 3 -- CLI for the real-NSE-session
validation report. READ-ONLY: reads the existing audit journal, writes
nothing, captures nothing, fabricates nothing (see
`app.orchestration.session_report`'s module docstring).

Usage:
    python -m scripts.session_report KAYNES JIOFIN RELIANCE
    python -m scripts.session_report --date 2026-08-31 KAYNES

Defaults to today (IST) when --date is omitted.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import date
from pathlib import Path

from app.domain.market.trading_calendar import classify_session
from app.orchestration.session_report import build_session_report, render_session_report
from app.persistence.jsonl_file import JsonlAuditJournalRepository
from app.utils.time import to_ist, utc_now

_JOURNAL_DIR = Path("data/persistence/audit_journal")


async def main() -> None:
    args = sys.argv[1:]
    session_date: date | None = None
    if args and args[0] == "--date":
        session_date = date.fromisoformat(args[1])
        args = args[2:]

    symbols = args
    if not symbols:
        print("usage: python -m scripts.session_report [--date YYYY-MM-DD] SYMBOL [SYMBOL ...]")
        return

    now = utc_now()
    if session_date is None:
        session_date = to_ist(now).date()

    session = classify_session(session_date)
    journal = JsonlAuditJournalRepository(_JOURNAL_DIR)
    report = await build_session_report(journal=journal, symbols=symbols, session_date=session_date, is_trading_day=session.is_trading_day, now=now)
    print(render_session_report(report))


if __name__ == "__main__":
    asyncio.run(main())
