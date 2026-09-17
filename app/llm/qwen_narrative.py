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
    "smart_money", "smart_money_score", "stop_loss", "target", "position_size",
    "direction", "hold",
})

_CLAIM_RE = re.compile(
    r"\b(buy now|sell now|go long|go short|target price|stop[\s-]?loss|"
    r"\d{1,3}\s*%\s*(confidence|probability)|institutional buyers? are accumulating)\b",
    re.IGNORECASE,
)

# Release gate (Section 16) -- forbidden CLAIMS in otherwise well-formed
# prose. `_CLAIM_RE` alone let "expected return", "predicts", "smart money",
# "a buy signal" or "will rally" through under allowed keys. Patterns are
# phrase-shaped, not bare words, because the deterministic facts Qwen must
# paraphrase legitimately contain "buy/sell quantity" and
# "research confidence WEAK"; rejecting those would only force fallback.
_FORBIDDEN_PROSE_RE = re.compile(
    r"\b("
    r"(strong\s+)?(buy|sell|accumulate)\s+(signal|call|recommendation|rating|opportunity)"
    r"|(you|traders?|investors?)\s+(should|could|may\s+want\s+to)\s+(buy|sell|enter|exit|short|go\s+long)"
    r"|expected\s+(return|gain|profit|upside)s?"
    r"|probabilit(y|ies)|likelihood|\d{1,3}\s*%\s*(chance|likely)"
    r"|confidence\s+(level|score|percentage|of\s+\d)"
    r"|predict(s|ed|ion|ions)?"
    r"|will\s+(likely\s+)?(rise|fall|rally|surge|drop|go\s+up|go\s+down|break\s*out|reach|hit)"
    r"|price\s+target|target\s+(of|at|level|zone)"
    r"|smart\s+money|operators?\s+(are|is)"
    r"|institutions?\s+(are|is)\s+(buying|selling|accumulating|loading)"
    r"|(traders?|institutions?|big\s+players?)\s+(intend|intends|want|wants|plan|plans)\s+to"
    r")\b",
    re.IGNORECASE,
)

_SYSTEM_PROMPT = (
    "You explain already-validated market-research facts supplied as E1, E2, ... "
    "Return JSON only with keys: summary, developing_observation, supporting_evidence, "
    "conflicting_evidence, missing_evidence, confirmation_condition, invalidation_condition, "
    "data_quality_note. Arrays are short strings that paraphrase supplied facts. "
    "Cite evidence IDs when possible. Do not invent prices, news, sectors, or evidence. "
    "Do not include recommendation, buy, sell, probability, confidence, expected_return, "
    "target_price, stop_loss, opportunity_score, prediction, or smart_money. "
    "Do not say BUY or SELL. Do not assign direction or position size."
)


@dataclass(frozen=True)
class QwenExplanation:
    summary: str
    developing_observation: str
    supporting_evidence: tuple[str, ...]
    conflicting_evidence: tuple[str, ...]
    missing_evidence: tuple[str, ...]
    confirmation_condition: str
    invalidation_condition: str
    data_quality_note: str
    model: str
    endpoint: str
    requested_at: datetime
    responded_at: datetime
    latency_ms: int
    source_evidence_ids: tuple[str, ...]
    raw_response: str

    @property
    def key_observations(self) -> tuple[str, ...]:
        return (self.developing_observation,) if self.developing_observation else ()

    @property
    def supporting_facts(self) -> tuple[str, ...]:
        return self.supporting_evidence

    @property
    def conflicting_facts(self) -> tuple[str, ...]:
        return self.conflicting_evidence

    @property
    def missing_information(self) -> tuple[str, ...]:
        return self.missing_evidence

    @property
    def questions_to_recheck(self) -> tuple[str, ...]:
        out: list[str] = []
        if self.confirmation_condition:
            out.append(self.confirmation_condition)
        if self.invalidation_condition:
            out.append(self.invalidation_condition)
        return tuple(out)


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


def _payload_text_blob(payload: dict[str, Any]) -> str:
    parts: list[str] = []
    for value in payload.values():
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):
            parts.extend(str(item) for item in value if isinstance(item, str))
    return " ".join(parts)


def validate_qwen_payload(payload: dict[str, Any]) -> str | None:
    """Return a rejection reason, or None if the payload is displayable."""
    lowered = {str(k).strip().casefold() for k in payload}
    banned = lowered & _PROHIBITED_KEYS
    if banned:
        return f"prohibited field(s): {', '.join(sorted(banned))}"
    if "summary" not in payload or not str(payload.get("summary") or "").strip():
        return "missing summary"
    blob = _payload_text_blob(payload)
    if _CLAIM_RE.search(blob):
        return "unsupported claim"
    if _FORBIDDEN_PROSE_RE.search(blob):
        return "prohibited claim"
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
    developing = str(payload.get("developing_observation") or "").strip()
    if not developing:
        observations = _as_str_tuple(payload.get("key_observations"))
        developing = observations[0] if observations else ""
    supporting = _as_str_tuple(payload.get("supporting_evidence") or payload.get("supporting_facts"))
    conflicting = _as_str_tuple(payload.get("conflicting_evidence") or payload.get("conflicting_facts"))
    missing = _as_str_tuple(payload.get("missing_evidence") or payload.get("missing_information"))
    confirm = str(payload.get("confirmation_condition") or "").strip()
    invalidate = str(payload.get("invalidation_condition") or "").strip()
    if not confirm or not invalidate:
        recheck = _as_str_tuple(payload.get("questions_to_recheck"))
        if not confirm and recheck:
            confirm = recheck[0]
        if not invalidate and len(recheck) > 1:
            invalidate = recheck[1]
    return QwenExplanation(
        summary=str(payload["summary"]).strip(),
        developing_observation=developing,
        supporting_evidence=supporting,
        conflicting_evidence=conflicting,
        missing_evidence=missing,
        confirmation_condition=confirm,
        invalidation_condition=invalidate,
        data_quality_note=str(payload.get("data_quality_note") or "").strip(),
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
        timeout_seconds: float = 8.0,
        health_timeout_seconds: float = 2.0,
        enabled: bool = True,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.health_timeout_seconds = health_timeout_seconds
        self.enabled = enabled
        self.endpoint = f"{self.base_url}/chat/completions"
        self.models_endpoint = f"{self.base_url}/models"

    async def connectivity_probe(self, client: httpx.AsyncClient) -> QwenResult:
        """Cheap reachability check -- never a generation request."""
        if not self.enabled:
            return QwenResult(
                ok=False, status="QWEN_NOT_CONFIGURED", explanation=None, detail="QWEN_ENABLED is false",
                latency_ms=0, model=self.model, endpoint=self.models_endpoint,
            )
        started = time.perf_counter()
        try:
            response = await client.get(self.models_endpoint, timeout=self.health_timeout_seconds)
        except httpx.TimeoutException:
            latency_ms = int((time.perf_counter() - started) * 1000)
            return QwenResult(
                ok=False, status="QWEN_TIMEOUT", explanation=None, detail="Qwen health timed out",
                latency_ms=latency_ms, model=self.model, endpoint=self.models_endpoint,
            )
        except httpx.HTTPError as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            return QwenResult(
                ok=False, status="QWEN_UNAVAILABLE", explanation=None, detail=str(exc),
                latency_ms=latency_ms, model=self.model, endpoint=self.models_endpoint,
            )
        latency_ms = int((time.perf_counter() - started) * 1000)
        if response.status_code < 200 or response.status_code >= 300:
            return QwenResult(
                ok=False, status="QWEN_UNAVAILABLE", explanation=None,
                detail=f"HTTP {response.status_code}",
                latency_ms=latency_ms, model=self.model, endpoint=self.models_endpoint,
            )
        return QwenResult(
            ok=True, status="QWEN_READY", explanation=None, detail="models endpoint reachable",
            latency_ms=latency_ms, model=self.model, endpoint=self.models_endpoint,
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
