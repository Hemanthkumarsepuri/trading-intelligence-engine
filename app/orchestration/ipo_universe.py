"""Real IPO universe (Phase 1) — paginates EVERY page of a real Upstox
`/v2/ipos` status bucket, not just page 1, with deterministic ordering,
duplicate protection, and per-page error isolation.

Reuses `ipo_intelligence.discover_ipos()` (itself a thin wrapper over
`UpstoxProvider.get_ipos()`) rather than calling the provider directly —
no new I/O primitive, only sequencing across pages.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.data.providers.exceptions import ProviderError
from app.data.providers.upstox_provider import UpstoxProvider
from app.domain.ipo.models import IPOIdentity
from app.orchestration.ipo_intelligence import discover_ipos
from app.utils.time import utc_now


@dataclass(frozen=True)
class IPOUniverseEntry:
    """One real IPO, with the status bucket it was found under and when
    THIS system retrieved it (not the same as any date on the identity
    itself, which reflects the IPO's own real bidding/listing dates)."""

    identity: IPOIdentity
    status: str
    retrieved_at: datetime


@dataclass(frozen=True)
class IPOUniverseFetchOutcome:
    """Part 1 -- per-status-bucket fetch result. Never silently drops a
    failed bucket: `errors` names exactly which status/page failed and
    why, while `entries` still carries everything that DID succeed."""

    entries: list[IPOUniverseEntry] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    pages_fetched: dict[str, int] = field(default_factory=dict)
    total_records: dict[str, int] = field(default_factory=dict)


_ALL_STATUSES = ("upcoming", "open", "closed", "listed")


async def fetch_ipo_universe(
    *, provider: UpstoxProvider, statuses: tuple[str, ...] = _ALL_STATUSES, max_pages_per_status: int = 20
) -> IPOUniverseFetchOutcome:
    """Fetches every page of every requested status bucket (bounded by
    `max_pages_per_status` as a safety cap against a pathological
    response, never a silent truncation -- if a bucket's real
    `total_pages` exceeds the cap, that is recorded in `errors`, not
    hidden). Deduplicates by `ipo_id` -- the SAME real IPO can legitimately
    appear once per status bucket it has ever been in (Upstox does not
    guarantee a listed IPO disappears from other buckets), so this keeps
    the FIRST occurrence found (in `statuses` order) and does not
    silently merge two occurrences' possibly-differing snapshots into
    one -- callers wanting the freshest copy should fetch `listed` before
    `closed` before `open` before `upcoming` (the default order already
    does this, newest-authoritative-state first).
    """
    entries: list[IPOUniverseEntry] = []
    errors: list[str] = []
    pages_fetched: dict[str, int] = {}
    total_records: dict[str, int] = {}
    seen_ids: set[str] = set()

    for status in statuses:
        page_number = 1
        pages_fetched[status] = 0
        while page_number <= max_pages_per_status:
            try:
                identities, page = await discover_ipos(provider=provider, status=status, page_number=page_number)
            except ProviderError as exc:
                errors.append(f"{status} page {page_number}: provider error: {exc}")
                break
            except ValueError as exc:
                errors.append(f"{status}: {exc}")
                break

            pages_fetched[status] += 1
            total_records[status] = page.total_records
            retrieved_at = utc_now()
            for identity in identities:
                if identity.ipo_id is not None and identity.ipo_id in seen_ids:
                    continue
                if identity.ipo_id is not None:
                    seen_ids.add(identity.ipo_id)
                entries.append(IPOUniverseEntry(identity=identity, status=status, retrieved_at=retrieved_at))

            if page_number >= page.total_pages:
                break
            page_number += 1
        else:
            errors.append(f"{status}: reached max_pages_per_status={max_pages_per_status} without exhausting all real pages -- some records were not fetched this call")

    return IPOUniverseFetchOutcome(entries=entries, errors=errors, pages_fetched=pages_fetched, total_records=total_records)
