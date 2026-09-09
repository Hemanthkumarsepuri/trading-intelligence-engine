"""Fail-open Qwen 2.5 explanation adapter (LM Studio OpenAI-compatible).

Reuses the Civil Interior / Arqon proven local setup:
`POST http://127.0.0.1:1234/v1/chat/completions` with model
`qwen2.5-coder-7b-instruct`, no `response_format` (that LM Studio build
rejects `json_object`).

Qwen never sets research state, freshness, confirmation, invalidation,
scores, probabilities, or BUY/SELL. On any failure the caller continues
with deterministic research unchanged.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from app.utils.time import utc_now

_PROHIBITED_KEYS = frozenset({
    "recommendation", "buy", "sell", "probability", "confidence",
    "expected_return", "target_price", "opportunity_score", "prediction",
    "smart_money",
})

_ALLOWED_KEYS = (
    "summary",
    "key_observations",
    "supporting_facts",
    "conflicting_facts",
    "missing_information",
    "questions_to_recheck",
)


_SYSTEM_PROMPT = (
    "You explain already-validated market-research facts. "
    "Return JSON only with keys: summary, key_observations, supporting_facts, "
    "conflicting_facts, missing_information, questions_to_recheck. "
    "Each list value is an array of short strings copied or paraphrased from "
    "the supplied facts. Do not invent prices, news, sectors, or evidence. "
    "Do not include recommendation, buy, sell, probability, confidence, "
    "expected_return, target_price, opportunity_score, prediction, or smart_money. "
    "Do not say BUY or SELL."
)


@dataclass(frozen=True)
class QwenExplanation:
    summary: str
    key_observations: tuple[str, ...]
    supporting_facts: tuple[str, ...]
    conflicting_facts: tuple[str, ...]
    missing_information: tuple[str, ...]
    questions_to_recheck: tuple[str, ...]
    model: str
    endpoint: str
    requested_at: datetime
    responded_at: datetime
    latency_ms: int
    source_evidence_ids: tuple[str, ...]
    raw_response: str


@dataclass(frozen=True)
class QwenResult:
    ok: bool
    status: str
    explanation: QwenExplanation | None
    detail: str | None
    latency_ms: int
    model: str
    endpoint: str


def _extract_json_object(content: str) -> dict[str, Any] | None:
    text = content.strip()
    try:
        decoded = json.loads(text)
        if isinstance(decoded, dict):
            return decoded
    except json.JSONDecodeError:
        pass
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if fenced:
        try:
            decoded = json.loads(fenced.group(1))
            if isinstance(decoded, dict):
                return decoded
        except json.JSONDecodeError:
            pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            decoded = json.loads(text[start : end + 1])
            if isinstance(decoded, dict):
                return decoded
        except json.JSONDecodeError:
            return None
    return None


def _as_str_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        item = value.strip()
        return (item,) if item else ()
    if isinstance(value, list):
        out: list[str] = []
        for entry in value:
            if isinstance(entry, str) and entry.strip():
                out.append(entry.strip())
        return tuple(out)
    return ()


def validate_qwen_payload(payload: dict[str, Any]) -> str | None:
    """Return a rejection reason, or None if the payload is displayable."""
    lowered = {str(k).strip().casefold() for k in payload}
    banned = lowered & _PROHIBITED_KEYS
    if banned:
        return f"prohibited field(s): {', '.join(sorted(banned))}"
    if "summary" not in payload or not str(payload.get("summary") or "").strip():
        return "missing summary"
    return None


def parse_qwen_explanation(
    *,
    content: str,
    model: str,
    endpoint: str,
    requested_at: datetime,
    responded_at: datetime,
    latency_ms: int,
    source_evidence_ids: tuple[str, ...],
) -> QwenExplanation:
    payload = _extract_json_object(content)
    if payload is None:
        raise ValueError("malformed JSON")
    reason = validate_qwen_payload(payload)
    if reason is not None:
        raise ValueError(reason)
    return QwenExplanation(
        summary=str(payload["summary"]).strip(),
        key_observations=_as_str_tuple(payload.get("key_observations")),
        supporting_facts=_as_str_tuple(payload.get("supporting_facts")),
        conflicting_facts=_as_str_tuple(payload.get("conflicting_facts")),
        missing_information=_as_str_tuple(payload.get("missing_information")),
        questions_to_recheck=_as_str_tuple(payload.get("questions_to_recheck")),
        model=model,
        endpoint=endpoint,
        requested_at=requested_at,
        responded_at=responded_at,
        latency_ms=latency_ms,
        source_evidence_ids=source_evidence_ids,
        raw_response=content,
    )


class QwenNarrativeAdapter:
    """Isolated HTTP boundary. Domain code must never import this for decisions."""

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:1234/v1",
        model: str = "qwen2.5-coder-7b-instruct",
        timeout_seconds: float = 45.0,
        enabled: bool = True,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.enabled = enabled
        self.endpoint = f"{self.base_url}/chat/completions"

    async def connectivity_probe(self, client: httpx.AsyncClient) -> QwenResult:
        return await self.explain(
            client,
            facts={"instruction": "Return JSON with summary exactly QWEN_TIRE_CONNECTIVITY_OK and empty arrays for the other keys."},
            source_evidence_ids=("connectivity_probe",),
            user_prompt="Return JSON. summary must be exactly QWEN_TIRE_CONNECTIVITY_OK.",
        )

    async def explain(
        self,
        client: httpx.AsyncClient,
        *,
        facts: dict[str, object],
        source_evidence_ids: tuple[str, ...],
        user_prompt: str | None = None,
    ) -> QwenResult:
        if not self.enabled:
            return QwenResult(
                ok=False, status="DISABLED", explanation=None, detail="QWEN_ENABLED is false",
                latency_ms=0, model=self.model, endpoint=self.endpoint,
            )
        requested_at = utc_now()
        started = time.perf_counter()
        body = {
            "model": self.model,
            "temperature": 0.1,
            "max_tokens": 600,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (user_prompt or "Explain these validated facts as JSON.")
                    + "\n\nVALIDATED_FACTS:\n"
                    + json.dumps(facts, default=str),
                },
            ],
        }
        try:
            response = await client.post(
                self.endpoint,
                json=body,
                headers={"Content-Type": "application/json"},
                timeout=self.timeout_seconds,
            )
        except httpx.TimeoutException:
            latency_ms = int((time.perf_counter() - started) * 1000)
            return QwenResult(
                ok=False, status="TIMEOUT", explanation=None, detail="Qwen request timed out",
                latency_ms=latency_ms, model=self.model, endpoint=self.endpoint,
            )
        except httpx.HTTPError as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            return QwenResult(
                ok=False, status="UNAVAILABLE", explanation=None, detail=str(exc),
                latency_ms=latency_ms, model=self.model, endpoint=self.endpoint,
            )
        latency_ms = int((time.perf_counter() - started) * 1000)
        responded_at = utc_now()
        if response.status_code < 200 or response.status_code >= 300:
            return QwenResult(
                ok=False, status="HTTP_ERROR", explanation=None,
                detail=f"HTTP {response.status_code}",
                latency_ms=latency_ms, model=self.model, endpoint=self.endpoint,
            )
        try:
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
            echoed_model = str(payload.get("model") or self.model)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            return QwenResult(
                ok=False, status="MALFORMED_RESPONSE", explanation=None, detail=str(exc),
                latency_ms=latency_ms, model=self.model, endpoint=self.endpoint,
            )
        if not isinstance(content, str) or not content.strip():
            return QwenResult(
                ok=False, status="EMPTY_RESPONSE", explanation=None, detail="empty assistant content",
                latency_ms=latency_ms, model=self.model, endpoint=self.endpoint,
            )
        try:
            explanation = parse_qwen_explanation(
                content=content, model=echoed_model, endpoint=self.endpoint,
                requested_at=requested_at, responded_at=responded_at, latency_ms=latency_ms,
                source_evidence_ids=source_evidence_ids,
            )
        except ValueError as exc:
            return QwenResult(
                ok=False, status="VALIDATION_REJECTED", explanation=None, detail=str(exc),
                latency_ms=latency_ms, model=self.model, endpoint=self.endpoint,
            )
        return QwenResult(
            ok=True, status="OK", explanation=explanation, detail=None,
            latency_ms=latency_ms, model=echoed_model, endpoint=self.endpoint,
        )
