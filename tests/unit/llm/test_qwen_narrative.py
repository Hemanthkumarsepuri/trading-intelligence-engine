from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from app.llm.qwen_narrative import (
    QwenNarrativeAdapter,
    parse_qwen_explanation,
    validate_qwen_payload,
)


def test_validate_rejects_prohibited_fields() -> None:
    assert validate_qwen_payload({"summary": "ok", "buy": "yes"}) == "prohibited field(s): buy"
    assert validate_qwen_payload({"summary": "ok", "hold": "yes"}) == "prohibited field(s): hold"
    assert validate_qwen_payload({"summary": "ok", "probability": 0.8}) == "prohibited field(s): probability"
    assert validate_qwen_payload({"key_observations": []}) == "missing summary"


def test_parse_accepts_fenced_json() -> None:
    content = """```json
{"summary": "hello", "key_observations": ["a"], "supporting_facts": [], "conflicting_facts": [], "missing_information": [], "questions_to_recheck": []}
```"""
    t = datetime(2026, 9, 9, tzinfo=UTC)
    parsed = parse_qwen_explanation(
        content=content, model="qwen2.5-coder-7b-instruct", endpoint="http://127.0.0.1:1234/v1/chat/completions",
        requested_at=t, responded_at=t, latency_ms=12, source_evidence_ids=("e1",),
    )
    assert parsed.summary == "hello"
    assert parsed.key_observations == ("a",)
    assert parsed.source_evidence_ids == ("e1",)


def test_validate_rejects_stop_loss_and_unsupported_claim() -> None:
    assert validate_qwen_payload({"summary": "ok", "stop_loss": "100"}) == "prohibited field(s): stop_loss"
    assert validate_qwen_payload({"summary": "Buy now this CE"}) == "unsupported claim"


def test_parse_accepts_green_schema_keys() -> None:
    content = '{"summary": "ok", "developing_observation": "compression", "supporting_evidence": ["E1"], "conflicting_evidence": [], "missing_evidence": ["chain"], "confirmation_condition": "hold", "invalidation_condition": "fail", "data_quality_note": "chain is HTTP receipt time"}'
    t = datetime(2026, 9, 9, tzinfo=UTC)
    parsed = parse_qwen_explanation(
        content=content, model="qwen2.5-coder-7b-instruct", endpoint="http://127.0.0.1:1234/v1/chat/completions",
        requested_at=t, responded_at=t, latency_ms=12, source_evidence_ids=("E1",),
    )
    assert parsed.developing_observation == "compression"
    assert parsed.supporting_evidence == ("E1",)
    assert parsed.confirmation_condition == "hold"


@pytest.mark.asyncio
async def test_adapter_fail_open_when_disabled() -> None:
    adapter = QwenNarrativeAdapter(enabled=False)
    async with httpx.AsyncClient() as client:
        result = await adapter.explain(client, facts={"x": 1}, source_evidence_ids=("a",))
    assert result.ok is False
    assert result.status == "DISABLED"


@pytest.mark.asyncio
async def test_adapter_timeout_is_fail_open() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("slow")

    adapter = QwenNarrativeAdapter(enabled=True, timeout_seconds=0.01)
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await adapter.explain(client, facts={"x": 1}, source_evidence_ids=("a",))
    assert result.ok is False
    assert result.status == "TIMEOUT"


@pytest.mark.asyncio
async def test_adapter_rejects_malformed_and_does_not_raise() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "not json at all"}}], "model": "qwen2.5-coder-7b-instruct"},
        )

    adapter = QwenNarrativeAdapter(enabled=True)
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await adapter.explain(client, facts={"x": 1}, source_evidence_ids=("a",))
    assert result.ok is False
    assert result.status == "VALIDATION_REJECTED"


@pytest.mark.asyncio
async def test_adapter_rejects_hallucinated_buy_field() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"summary": "x", "buy": "CE"}'}}],
                "model": "qwen2.5-coder-7b-instruct",
            },
        )

    adapter = QwenNarrativeAdapter(enabled=True)
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await adapter.explain(client, facts={"pattern": "OI_MIGRATION"}, source_evidence_ids=("e",))
    assert result.ok is False
    assert "buy" in (result.detail or "")


@pytest.mark.parametrize(
    "text",
    [
        "This is a strong buy signal on RELIANCE.",
        "Expected return over the week is meaningful.",
        "There is a high probability of a breakout.",
        "The model predicts continuation.",
        "Smart money is positioning here.",
        "Institutions are accumulating the stock.",
        "Price will likely rally past resistance.",
        "A price target near 1500 applies.",
        "Traders want to push this higher.",
        "You should buy before the open.",
    ],
)
def test_validate_rejects_forbidden_claims_in_prose_under_allowed_keys(text: str) -> None:
    assert validate_qwen_payload({"summary": text}) in {"prohibited claim", "unsupported claim"}


@pytest.mark.parametrize(
    "text",
    [
        "Order flow shows a buy/sell quantity imbalance of 1.4, which is not directional by itself.",
        "Research confidence is WEAK because chain evidence is missing.",
        "Confirmation would be price holding beyond the nearby resistance level.",
        "Invalidation would be a break below the recorded support.",
        "Historically this pattern showed follow-through in 40 of 120 determined observations; this is not a forecast.",
    ],
)
def test_validate_accepts_factual_paraphrase_of_deterministic_evidence(text: str) -> None:
    assert validate_qwen_payload({"summary": text}) is None
