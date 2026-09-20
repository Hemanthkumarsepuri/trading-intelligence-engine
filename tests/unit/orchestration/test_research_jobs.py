from __future__ import annotations

import asyncio

import pytest

from app.orchestration.research_jobs import ResearchJob, ResearchJobRegistry


@pytest.mark.asyncio
async def test_registry_runs_one_job_and_records_progress() -> None:
    registry = ResearchJobRegistry()

    async def runner(job: ResearchJob) -> dict[str, object]:
        job.stage = "stage1"
        job.processed = 3
        job.total = 10
        await asyncio.sleep(0.05)
        return {"ok": True}

    first = await registry.start(symbols=None, runner=runner)
    assert first.as_dict()["scan_kind"] == "F&O_UNIVERSE"
    assert first.as_dict()["symbols"] is None
    second = await registry.start(symbols=None, runner=runner)
    assert first.job_id == second.job_id
    await asyncio.sleep(0.15)
    latest = registry.latest()
    assert latest is not None
    assert latest.status == "COMPLETE"
    assert latest.result == {"ok": True}


@pytest.mark.asyncio
async def test_explicit_symbol_job_is_labeled_explicit_not_universe() -> None:
    registry = ResearchJobRegistry()

    async def runner(job: ResearchJob) -> dict[str, object]:
        return {"scan_mode": "EXPLICIT_SYMBOL_QUERY"}

    job = await registry.start(symbols=("RELIANCE", "KAYNES"), runner=runner)
    payload = job.as_dict()
    assert payload["scan_kind"] == "EXPLICIT"
    assert payload["symbols"] == ["RELIANCE", "KAYNES"]
    await asyncio.sleep(0.05)
    latest = registry.latest()
    assert latest is not None
    assert latest.as_dict()["scan_kind"] == "EXPLICIT"


@pytest.mark.asyncio
async def test_failed_job_does_not_crash_registry() -> None:
    registry = ResearchJobRegistry()

    async def runner(job: ResearchJob) -> dict[str, object]:
        raise RuntimeError("boom")

    job = await registry.start(symbols=None, runner=runner)
    await asyncio.sleep(0.05)
    stored = registry.get(job.job_id)
    assert stored is not None
    assert stored.status == "FAILED"
    assert stored.error == "boom"
