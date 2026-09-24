"""Sprint 3.5 -- the trigger that makes the forward outcome sweep automatic.

    lifespan-owned asyncio loop  ->  `runner(as_of=clock())`
        -> `sweep_all_due_research_outcomes_detailed()`   (the canonical sweep)
        -> canonical due-query + canonical outcome engine + idempotent persistence

THE SCHEDULER IS ONLY A TRIGGER. It contains no research logic: it never
decides whether an observation is due (the outcome engine's
`due_research_checkpoints` does -- a horizon is due at the target session's
close), never computes an outcome, never reads or writes T0, and never passes
anything but the real clock as `as_of` (so it cannot make a future horizon
"available"). Persistence is the source of truth.

Why a plain loop and not APScheduler/Celery/Redis: the deployment is one
`uvicorn` process (Railway, `restartPolicyType=ON_FAILURE`); the state that
matters lives in append-only JSONL files, and the sweep is idempotent
(`save_checkpoint_once`, keyed on observation + horizon). So the trigger only
has to be *eventually* invoked: a missed tick, a crash, a redeploy or a period
of downtime is recovered by the next sweep, because "what is due" is recomputed
from persisted observations and checkpoints every time. Nothing in memory here
is needed for correctness -- only for observability.

Overlap: an `asyncio.Lock` skips (and counts) a tick that arrives while a sweep
is running. That guard is process-local by design; it is not a distributed
lock. If two processes ever shared one data directory, a duplicate sweep would
still be safe for the same reason as a duplicate tick (the persistence refuses a
second checkpoint for a horizon), but cross-process atomicity of the JSONL
files is a known, deferred limitation (unchanged by this sprint).

Status honesty: `SCHEDULER_RAN` (a sweep completed without raising) is not
"outcomes are complete" and is not "data was sufficient" -- the status keeps
those apart and never reports a green it cannot back.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from enum import Enum
from typing import Any

from app.orchestration.research_outcome import OutcomeSweepResult
from app.utils.time import utc_now

_LOG = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 900  # 15 minutes: horizons are due at 15:30 IST, so a tick lands within ~15 min of it
DEFAULT_INITIAL_DELAY_SECONDS = 30  # first tick shortly after startup: recovers anything that came due while down

SweepRunner = Callable[[datetime], Awaitable[OutcomeSweepResult]]
Clock = Callable[[], datetime]
Sleep = Callable[[float], Awaitable[None]]


class SweepHealth(str, Enum):
    NOT_CONFIGURED = "NOT_CONFIGURED"  # no provider/credentials: the sweep cannot run
    DISABLED = "DISABLED"  # switched off by configuration
    NEVER_RAN = "NEVER_RAN"  # started, no sweep attempted yet
    OPERATIONAL = "OPERATIONAL"  # the last sweep ran and left nothing unresolved that it could have resolved
    DEGRADED = "DEGRADED"  # the last sweep ran, but an observation failed or a due horizon could not be evaluated
    FAILED = "FAILED"  # the last sweep raised


class OutcomeSweepScheduler:
    def __init__(
        self, runner: SweepRunner, *, clock: Clock = utc_now, sleep: Sleep = asyncio.sleep,
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS, initial_delay_seconds: float = DEFAULT_INITIAL_DELAY_SECONDS,
    ) -> None:
        self._runner = runner
        self._clock = clock
        self._sleep = sleep
        self._interval = interval_seconds
        self._initial_delay = initial_delay_seconds
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._health = SweepHealth.NEVER_RAN
        self._attempts = 0
        self._overlaps_skipped = 0
        self._consecutive_failures = 0
        self._last_attempt_at: datetime | None = None
        self._last_success_at: datetime | None = None
        self._last_error_type: str | None = None
        self._last_result: OutcomeSweepResult | None = None

    # -- state ---------------------------------------------------------------

    def mark_unavailable(self, health: SweepHealth) -> None:
        """Record why no loop is running (`NOT_CONFIGURED` / `DISABLED`)."""
        self._health = health

    @property
    def health(self) -> SweepHealth:
        return self._health

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    # -- the one function the loop, tests and any manual invocation share ----

    async def run_once(self) -> SweepHealth:
        """One trigger. Never raises (except cancellation). A tick that arrives
        while a sweep is running is skipped and counted."""
        if self._lock.locked():
            self._overlaps_skipped += 1
            _LOG.info("outcome sweep tick skipped: a sweep is already running")
            return self._health
        async with self._lock:
            as_of = self._clock()  # the real clock only -- never a caller-chosen or future instant
            self._attempts += 1
            self._last_attempt_at = as_of
            try:
                result = await self._runner(as_of)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 -- the trigger must survive any sweep failure
                self._consecutive_failures += 1
                self._last_error_type = type(exc).__name__  # type only: no stack trace, no message
                self._health = SweepHealth.FAILED
                _LOG.warning("outcome sweep failed: %s", type(exc).__name__)
                return self._health
            self._consecutive_failures = 0
            self._last_error_type = None
            self._last_success_at = as_of
            self._last_result = result
            degraded = result.observations_failed > 0 or result.horizons_due_unresolved > 0
            self._health = SweepHealth.DEGRADED if degraded else SweepHealth.OPERATIONAL
            _LOG.info(
                "outcome sweep ran: examined=%d created=%d insufficient_created=%d unresolved=%d failed=%d",
                result.observations_examined, result.horizons_created, result.horizons_created_insufficient,
                result.horizons_due_unresolved, result.observations_failed,
            )
            return self._health

    # -- lifecycle -----------------------------------------------------------

    async def _loop(self) -> None:
        await self._sleep(self._initial_delay)
        while True:
            await self.run_once()
            await self._sleep(self._interval)

    def start(self) -> None:
        if self.running:
            return
        self._task = asyncio.get_running_loop().create_task(self._loop(), name="tire-outcome-sweep")

    async def stop(self) -> None:
        """Cancel the loop and wait for it; leaves no orphan task."""
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    # -- observability -------------------------------------------------------

    def status_payload(self) -> dict[str, Any]:
        result = self._last_result
        outcomes_complete: bool | None = None
        if result is not None and result.observations_total > 0:
            # True only when no persisted observation is still awaiting a horizon AND no persisted
            # horizon lacked its price data. A sweep having run proves neither.
            outcomes_complete = result.observations_still_awaiting == 0 and result.insufficient_horizons_total == 0
        return {
            "health": self._health.value,
            "running": self.running,
            "interval_seconds": self._interval,
            "attempts": self._attempts,
            "overlaps_skipped": self._overlaps_skipped,
            "consecutive_failures": self._consecutive_failures,
            "last_attempt_at": self._last_attempt_at.isoformat() if self._last_attempt_at else None,
            "last_success_at": self._last_success_at.isoformat() if self._last_success_at else None,
            "last_error_type": self._last_error_type,
            "last_result": None if result is None else {
                "as_of": result.as_of.isoformat(),
                "observations_total": result.observations_total,
                "observations_examined": result.observations_examined,
                "observations_awaiting_horizons": result.observations_still_awaiting,
                "observations_failed": result.observations_failed,
                "horizons_created": result.horizons_created,
                "horizons_created_insufficient": result.horizons_created_insufficient,
                "horizons_already_complete": result.horizons_already_complete,
                "horizons_not_yet_due": result.horizons_not_yet_due,
                "horizons_due_unresolved": result.horizons_due_unresolved,
                "horizons_data_insufficient_total": result.insufficient_horizons_total,
            },
            "outcomes_complete": outcomes_complete,
            "note": (
                "health describes the trigger, not the research: a sweep having run is not outcomes_complete, and "
                "outcomes_complete is false while any horizon is pending or any checkpoint lacked price data."
            ),
        }
