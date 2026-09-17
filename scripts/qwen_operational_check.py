"""Qwen 2.5 operational check -- the five failure-safety paths, over REAL HTTP.

Release gate (Section 15). Drives the production `QwenNarrativeAdapter`
(the same object `POST /api/research/explain` uses) through:

  A  AVAILABLE   the real local LM Studio endpoint (skipped, and reported
                 as such, when it is not reachable), plus A_SUCCESS_PATH_STUB:
                 the displayable-output path through the same adapter,
                 parser and validator, which a real generation on this
                 hardware is too slow to reach (docs/TIRE_QWEN.md)
  B  UNAVAILABLE a closed local port
  C  TIMEOUT     a local stub that accepts the request and never answers in time
  D  MALFORMED   a local stub returning non-JSON assistant content
  E  PROHIBITED  a local stub returning schema-valid JSON containing
                 forbidden claims (BUY, probability, target, smart money)

Every non-A case must come back `ok=False` without raising, which is what
makes the caller show the deterministic explanation. Latencies are measured,
not assumed. Nothing here is a trading decision; the stubs only exercise the
adapter's failure handling.

Run:
    python -m scripts.qwen_operational_check [--real-timeout SECONDS]
"""

from __future__ import annotations

import asyncio
import json
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx

from app.config.settings import settings
from app.llm.qwen_narrative import QwenNarrativeAdapter, QwenResult

_FACTS: dict[str, object] = {
    "symbol": "RELIANCE",
    "research_state": "WATCH",
    "E1": "M15 EMA(9,21,50) ascending",
    "E2": "price above session VWAP",
    "E3": "option chain unavailable for this instant",
    "confirmation": "price holds beyond the nearby resistance on M15",
    "invalidation": "price breaks the recorded support",
}
_IDS = ("E1", "E2", "E3")


def _completion(content: str) -> bytes:
    return json.dumps({"model": "stub", "choices": [{"message": {"role": "assistant", "content": content}}]}).encode()


_STUB_BODIES: dict[str, bytes] = {
    # The SUCCESS path, exercised through the same adapter, parser and
    # validator the real model's output goes through. This is not a claim
    # that the local model produced it -- see A_AVAILABLE for that, and
    # `docs/TIRE_QWEN.md` for the measured local throughput that makes a
    # full real generation impractical on this hardware.
    "/valid/v1/chat/completions": _completion(json.dumps({
        "summary": "RELIANCE shows an early, unconfirmed structure: M15 trend and VWAP agree, but no options evidence exists for this instant.",
        "developing_observation": "E1 and E2 point the same way while E3 is missing.",
        "supporting_evidence": ["E1: M15 EMA(9,21,50) ascending", "E2: price above session VWAP"],
        "conflicting_evidence": [],
        "missing_evidence": ["E3: option chain unavailable for this instant"],
        "confirmation_condition": "price holds beyond the nearby resistance on M15",
        "invalidation_condition": "price breaks the recorded support",
        "data_quality_note": "price-only evidence; no chain or futures input",
    })),
    "/malformed/v1/chat/completions": _completion("Sure! Here is my analysis of RELIANCE: it looks interesting."),
    "/prohibited/v1/chat/completions": _completion(json.dumps({
        "summary": "RELIANCE is a strong buy signal with high probability of reaching the price target.",
        "developing_observation": "Smart money is accumulating.",
        "supporting_evidence": ["E1"], "conflicting_evidence": [], "missing_evidence": ["E3"],
        "confirmation_condition": "holds above resistance", "invalidation_condition": "breaks support",
        "data_quality_note": "chain unavailable",
    })),
}


class _Stub(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        if self.path.startswith("/slow/"):
            time.sleep(5)
        body = _STUB_BODIES.get(self.path, _completion("{}"))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _case(name: str, adapter: QwenNarrativeAdapter, client: httpx.AsyncClient) -> dict[str, object]:
    started = time.perf_counter()
    try:
        result: QwenResult | None = await adapter.explain(client, facts=_FACTS, source_evidence_ids=_IDS)
        raised = None
    except Exception as exc:  # noqa: BLE001 -- the whole point is to prove nothing escapes
        result, raised = None, f"{type(exc).__name__}: {exc}"
    wall_ms = int((time.perf_counter() - started) * 1000)
    return {
        "case": name, "ok": result.ok if result else None, "status": result.status if result else "RAISED",
        "detail": (result.detail or "")[:120] if result else raised, "wall_ms": wall_ms,
        "fails_safe": raised is None, "summary": result.explanation.summary[:160] if result and result.explanation else None,
    }


async def main_async(real_timeout: float) -> list[dict[str, object]]:
    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), _Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    closed_port = _free_port()
    stub = f"http://127.0.0.1:{port}"
    results: list[dict[str, object]] = []
    async with httpx.AsyncClient() as client:
        real = QwenNarrativeAdapter(
            base_url=settings.qwen_base_url, model=settings.qwen_model, timeout_seconds=real_timeout,
            health_timeout_seconds=settings.qwen_health_timeout_seconds,
        )
        probe_started = time.perf_counter()
        health = await real.connectivity_probe(client)
        results.append({
            "case": "HEALTH", "ok": health.ok, "status": health.status, "detail": health.detail,
            "wall_ms": int((time.perf_counter() - probe_started) * 1000), "fails_safe": True, "summary": None,
        })
        if health.ok:
            results.append(await _case("A_AVAILABLE", real, client))
        else:
            results.append({
                "case": "A_AVAILABLE", "ok": None, "status": "NOT_RUN", "detail": f"endpoint not reachable ({health.status})",
                "wall_ms": 0, "fails_safe": True, "summary": None,
            })
        results.append(await _case("A_SUCCESS_PATH_STUB", QwenNarrativeAdapter(base_url=f"{stub}/valid/v1"), client))
        results.append(await _case("B_UNAVAILABLE", QwenNarrativeAdapter(base_url=f"http://127.0.0.1:{closed_port}/v1"), client))
        results.append(await _case("C_TIMEOUT", QwenNarrativeAdapter(base_url=f"{stub}/slow/v1", timeout_seconds=1.0), client))
        results.append(await _case("D_MALFORMED", QwenNarrativeAdapter(base_url=f"{stub}/malformed/v1"), client))
        results.append(await _case("E_PROHIBITED", QwenNarrativeAdapter(base_url=f"{stub}/prohibited/v1"), client))
    server.shutdown()
    return results


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    real_timeout = float(args[args.index("--real-timeout") + 1]) if "--real-timeout" in args else settings.qwen_timeout_seconds
    results = asyncio.run(main_async(real_timeout))
    for row in results:
        print(json.dumps(row))
    failures = [r for r in results if r["case"] in {"B_UNAVAILABLE", "C_TIMEOUT", "D_MALFORMED", "E_PROHIBITED"} and (r["ok"] or not r["fails_safe"])]
    failures += [r for r in results if r["case"] == "A_SUCCESS_PATH_STUB" and not r["ok"]]
    print("FAIL-SAFE: " + ("ALL PASS" if not failures else f"{len(failures)} FAILED"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
