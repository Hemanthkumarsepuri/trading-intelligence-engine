"""Live F&O scan certification.

Refuses to run unless production (or TIRE_API_BASE) reports session_window=OPEN.
Never treats CLOSED-session last prints as a live scan.

Usage:
  python -m scripts.certify_live_fno_scan
  python -m scripts.certify_live_fno_scan --api https://api-production-983e.up.railway.app
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import Any

DEFAULT_API = "https://api-production-983e.up.railway.app"
ORIGIN = "https://tire-research-terminal.netlify.app"


def _req(api: str, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
    data = None if body is None else json.dumps(body).encode()
    headers = {"Accept": "application/json", "Origin": ORIGIN}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(api + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=120) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            parsed: Any = json.loads(raw)
        except json.JSONDecodeError:
            parsed = raw[:800]
        return exc.code, parsed


def _session_window(health: dict[str, Any] | None) -> str:
    if not isinstance(health, dict):
        return "UNKNOWN"
    window = health.get("session_window")
    if isinstance(window, dict):
        return str(window.get("session_window") or "UNKNOWN")
    return str(window or "UNKNOWN")


def certify(api: str) -> int:
    now = datetime.now(UTC).isoformat()
    status, health = _req(api, "GET", "/api/health")
    session = _session_window(health if isinstance(health, dict) else None)
    observed_status, observed = _req(api, "GET", "/api/market/observed")
    print(json.dumps({
        "as_of_utc": now,
        "api": api,
        "health_status": status,
        "session_window": session,
        "observed_status": observed_status,
        "is_trading_day": (observed or {}).get("is_trading_day") if isinstance(observed, dict) else None,
        "calendar_date_ist": (observed or {}).get("calendar_date_ist") if isinstance(observed, dict) else None,
        "broker_execution": (health or {}).get("broker_execution") if isinstance(health, dict) else None,
    }, indent=2))
    if session != "OPEN":
        print(
            "FULL F&O LIVE VALIDATION = PENDING — MARKET CLOSED",
            file=sys.stderr,
        )
        print(
            "This script will not start a universe scan or relabel last-observed prints as live.",
            file=sys.stderr,
        )
        return 2
    start_status, job = _req(api, "POST", "/api/research/jobs/discover")
    if start_status != 200 or not isinstance(job, dict):
        print("FAILED to start discover job", start_status, job, file=sys.stderr)
        return 1
    job_id = job.get("id") or job.get("job_id")
    print("started", job_id, job.get("status"))
    deadline = time.time() + 45 * 60
    latest: Any = job
    while time.time() < deadline:
        st, latest = _req(api, "GET", "/api/research/jobs/latest")
        state = (latest or {}).get("status") if isinstance(latest, dict) else None
        print("poll", st, state, (latest or {}).get("message") if isinstance(latest, dict) else "")
        if state in {"COMPLETED", "FAILED", "ERROR"}:
            break
        time.sleep(10)
    print(json.dumps(latest, indent=2, default=str)[:12000])
    if isinstance(latest, dict) and latest.get("status") == "COMPLETED":
        return 0
    return 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Certify a live NSE F&O universe scan.")
    parser.add_argument("--api", default=DEFAULT_API)
    args = parser.parse_args()
    raise SystemExit(certify(args.api.rstrip("/")))


if __name__ == "__main__":
    main()
