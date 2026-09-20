"""Drive Playwright MCP (HTTP) against the local TIRE dashboard."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

MCP = "http://localhost:8931/mcp"
OUT = Path(__file__).resolve().parent


class Mcp:
    def __init__(self) -> None:
        self.sid = ""
        self._id = 1

    def _post(self, payload: dict[str, object], timeout: int = 90) -> tuple[dict[str, str], str]:
        data = json.dumps(payload).encode()
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.sid:
            headers["mcp-session-id"] = self.sid
        req = urllib.request.Request(MCP, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            hdrs = {k.lower(): v for k, v in resp.headers.items()}
            body = resp.read().decode("utf-8", "replace")
        return hdrs, body

    def start(self) -> None:
        hdrs, _body = self._post({
            "jsonrpc": "2.0",
            "id": self._id,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "tire-uat", "version": "1.0"},
            },
        })
        self.sid = hdrs.get("mcp-session-id", "")
        self._id += 1
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def rpc(self, method: str, params: dict[str, object] | None = None, timeout: int = 90) -> object:
        self._id += 1
        _, body = self._post({
            "jsonrpc": "2.0",
            "id": self._id,
            "method": method,
            "params": params or {},
        }, timeout=timeout)
        text = body
        if "data: " in body:
            chunks = [ln[6:] for ln in body.splitlines() if ln.startswith("data: ")]
            text = chunks[-1] if chunks else body
        parsed = json.loads(text)
        if "error" in parsed:
            raise RuntimeError(parsed["error"])
        return parsed.get("result", parsed)

    def call(self, name: str, arguments: dict[str, object] | None = None, timeout: int = 90) -> object:
        return self.rpc("tools/call", {"name": name, "arguments": arguments or {}}, timeout=timeout)


def text_of(result: object) -> str:
    if not isinstance(result, dict):
        return str(result)
    content = result.get("content")
    if isinstance(content, list):
        return "\n".join(str(part.get("text", part)) for part in content if isinstance(part, dict))
    return json.dumps(result)[:4000]


def main() -> None:
    mcp = Mcp()
    mcp.start()
    print("SESSION", mcp.sid)
    tools = mcp.rpc("tools/list")
    tool_names: list[str] = []
    if isinstance(tools, dict):
        raw_tools = tools.get("tools") or []
        if isinstance(raw_tools, list):
            tool_names = [str(t.get("name")) for t in raw_tools if isinstance(t, dict)]
    report: dict[str, object] = {
        "mcp": "Playwright HTTP :8931",
        "transport": "HTTP",
        "tools": tool_names,
        "journeys": [],
    }

    mcp.call("browser_resize", {"width": 1280, "height": 800})
    mcp.call("browser_navigate", {"url": "http://127.0.0.1:8000/#screener"})
    time.sleep(1.2)
    mcp.call("browser_snapshot", {})
    mcp.call("browser_take_screenshot", {"filename": str(OUT / "mcp-home-1280.png")})
    geo = text_of(mcp.call("browser_evaluate", {
        "function": """() => ({
          header: document.querySelector('header.app-chrome')?.getBoundingClientRect().height,
          overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
          scrollW: document.documentElement.scrollWidth,
          clientW: document.documentElement.clientWidth,
          nifty: document.getElementById('glance-nifty')?.textContent,
          niftyD: document.getElementById('glance-nifty-d')?.textContent,
          session: document.getElementById('session-pill')?.textContent,
          data: document.getElementById('data-pill')?.textContent,
          refresh: document.getElementById('refresh-interval')?.value,
          empty: document.querySelector('#screener-table-host')?.innerText?.slice(0,240),
        })"""
    }))
    cons = text_of(mcp.call("browser_console_messages", {"level": "warning", "all": True}))
    report["journeys"].append({"name": "home-1280", "geo": geo, "console": cons[:1500]})
    print("HOME1280", geo)

    mcp.call("browser_resize", {"width": 1440, "height": 900})
    mcp.call("browser_navigate", {"url": "http://127.0.0.1:8000/?v=final#screener"})
    time.sleep(0.8)
    geo1440 = text_of(mcp.call("browser_evaluate", {
        "function": """() => ({
          header: document.querySelector('header.app-chrome')?.getBoundingClientRect().height,
          overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
          lastNav: document.querySelector('.primary-nav button:last-child')?.getBoundingClientRect().right,
          lastGlance: document.querySelector('#market-glance > :last-child')?.getBoundingClientRect().right,
        })"""
    }))
    mcp.call("browser_take_screenshot", {"filename": str(OUT / "mcp-home-1440.png")})
    report["journeys"].append({"name": "home-1440", "geo": geo1440})
    print("HOME1440", geo1440)

    # explicit real scan of two names — not hardcoded production rows
    mcp.call("browser_evaluate", {
        "function": """() => {
          const input = document.getElementById('research-symbols');
          if (input) input.value = 'RELIANCE,KAYNES';
          document.getElementById('research-btn')?.click();
          return 'scan-started';
        }"""
    })
    scan_state = "pending"
    for _ in range(90):
        time.sleep(2)
        scan_state = text_of(mcp.call("browser_evaluate", {
            "function": """() => ({
              progress: document.getElementById('scan-progress-line')?.textContent,
              rows: document.querySelectorAll('table.screener tbody tr').length,
              host: document.querySelector('#screener-table-host')?.innerText?.slice(0,400)
            })"""
        }))
        if "RELIANCE" in scan_state or "KAYNES" in scan_state or "Scan complete" in scan_state:
            break
    mcp.call("browser_take_screenshot", {"filename": str(OUT / "mcp-screener-scan.png")})
    report["journeys"].append({"name": "scan", "state": scan_state})
    print("SCAN", scan_state[:800])

    # open RELIANCE if present else type analyze
    mcp.call("browser_evaluate", {
        "function": """() => {
          const q = document.getElementById('query');
          if (q) q.value = 'RELIANCE';
          window.analyze('RELIANCE');
          return 'analyze-reliance';
        }"""
    })
    time.sleep(8)
    rel = text_of(mcp.call("browser_evaluate", {
        "function": """() => ({
          hero: document.getElementById('symbol-hero')?.innerText?.slice(0,800),
          loading: document.getElementById('loading')?.style.display,
          view: document.querySelector('#view-symbol')?.className
        })"""
    }))
    mcp.call("browser_take_screenshot", {"filename": str(OUT / "mcp-symbol-reliance.png")})
    report["journeys"].append({"name": "symbol-reliance", "state": rel})
    print("RELIANCE", rel[:800])

    # replacement
    mcp.call("browser_evaluate", {
        "function": """() => { window.analyze('KAYNES'); return document.getElementById('symbol-hero')?.innerText?.slice(0,400); }"""
    })
    time.sleep(0.4)
    mid = text_of(mcp.call("browser_evaluate", {
        "function": """() => document.getElementById('loading')?.innerText + ' || ' + document.getElementById('symbol-hero')?.innerText"""
    }))
    mcp.call("browser_take_screenshot", {"filename": str(OUT / "mcp-symbol-kaynes-loading.png")})
    time.sleep(10)
    kay = text_of(mcp.call("browser_evaluate", {
        "function": """() => ({
          hero: document.getElementById('symbol-hero')?.innerText?.slice(0,800),
          hasReliance: (document.getElementById('symbol-hero')?.innerText||'').includes('RELIANCE'),
          hasKaynes: (document.getElementById('symbol-hero')?.innerText||'').includes('KAYNES')
        })"""
    }))
    mcp.call("browser_take_screenshot", {"filename": str(OUT / "mcp-symbol-kaynes.png")})
    report["journeys"].append({"name": "replacement-mid", "state": mid, "after": kay})
    print("REPLACE_MID", mid[:500])
    print("REPLACE_AFTER", kay[:800])

    mcp.call("browser_evaluate", {
        "function": """() => { window.analyze('ZZZXNOTREAL'); return 'fail'; }"""
    })
    time.sleep(4)
    fail = text_of(mcp.call("browser_evaluate", {
        "function": """() => ({
          hero: document.getElementById('symbol-hero')?.innerText?.slice(0,500),
          err: document.getElementById('error-box')?.innerText?.slice(0,300)
        })"""
    }))
    mcp.call("browser_take_screenshot", {"filename": str(OUT / "mcp-symbol-fail.png")})
    report["journeys"].append({"name": "failure", "state": fail})
    print("FAIL", fail[:500])

    mcp.call("browser_evaluate", {"function": "() => { window.showView('history'); return 'history'; }"})
    time.sleep(1.5)
    hist = text_of(mcp.call("browser_evaluate", {
        "function": """() => document.getElementById('view-history')?.innerText?.slice(0,500)"""
    }))
    mcp.call("browser_take_screenshot", {"filename": str(OUT / "mcp-history-1280.png")})
    mcp.call("browser_evaluate", {"function": "() => { window.showView('patterns'); return 'patterns'; }"})
    time.sleep(1.5)
    pat = text_of(mcp.call("browser_evaluate", {
        "function": """() => document.getElementById('view-patterns')?.innerText?.slice(0,600)"""
    }))
    mcp.call("browser_take_screenshot", {"filename": str(OUT / "mcp-patterns-1280.png")})
    report["journeys"].append({"name": "history", "state": hist})
    report["journeys"].append({"name": "patterns", "state": pat})
    print("HISTORY", hist[:400])
    print("PATTERNS", pat[:400])

    mcp.call("browser_resize", {"width": 390, "height": 844})
    mcp.call("browser_navigate", {"url": "http://127.0.0.1:8000/?m=1#screener"})
    time.sleep(1)
    mob = text_of(mcp.call("browser_evaluate", {
        "function": """() => ({
          header: document.querySelector('header.app-chrome')?.getBoundingClientRect().height,
          overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
          scrollW: document.documentElement.scrollWidth,
          first: document.body.innerText.slice(0,400)
        })"""
    }))
    mcp.call("browser_take_screenshot", {"filename": str(OUT / "mcp-mobile-390.png")})
    mcp.call("browser_evaluate", {"function": "() => { window.showView('history'); return 1; }"})
    time.sleep(0.8)
    mcp.call("browser_take_screenshot", {"filename": str(OUT / "mcp-history-390.png")})
    mcp.call("browser_evaluate", {"function": "() => { window.showView('patterns'); return 1; }"})
    time.sleep(0.8)
    mcp.call("browser_take_screenshot", {"filename": str(OUT / "mcp-patterns-390.png")})
    cons2 = text_of(mcp.call("browser_console_messages", {"level": "error", "all": True}))
    report["journeys"].append({"name": "mobile", "geo": mob, "console_errors": cons2[:1500]})
    print("MOBILE", mob)
    print("CONSOLE_ERR", cons2[:800])

    mcp.call("browser_resize", {"width": 360, "height": 800})
    time.sleep(0.5)
    geo360 = text_of(mcp.call("browser_evaluate", {
        "function": """() => ({
          header: document.querySelector('header.app-chrome')?.getBoundingClientRect().height,
          overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
          scrollW: document.documentElement.scrollWidth,
          clientW: document.documentElement.clientWidth
        })"""
    }))
    mcp.call("browser_take_screenshot", {"filename": str(OUT / "mcp-mobile-360.png")})
    report["journeys"].append({"name": "mobile-360", "geo": geo360})
    print("MOBILE360", geo360)

    mcp.call("browser_resize", {"width": 1280, "height": 800})
    mcp.call("browser_navigate", {"url": "http://127.0.0.1:8000/#screener"})
    time.sleep(1)
    race = text_of(mcp.call("browser_evaluate", {
        "function": """() => {
          window.analyze('RELIANCE');
          window.analyze('KAYNES');
          window.analyze('BEL');
          return document.getElementById('symbol-hero')?.innerText?.slice(0,200);
        }"""
    }))
    time.sleep(16)
    race_after = text_of(mcp.call("browser_evaluate", {
        "function": """() => ({
          hero: document.getElementById('symbol-hero')?.innerText?.slice(0,400),
          hasReliance: (document.getElementById('symbol-hero')?.innerText||'').includes('RELIANCE'),
          hasKaynes: (document.getElementById('symbol-hero')?.innerText||'').includes('KAYNES'),
          hasBel: (document.getElementById('symbol-hero')?.innerText||'').includes('BEL')
        })"""
    }))
    report["journeys"].append({"name": "race", "mid": race, "after": race_after})
    print("RACE", race_after[:800])

    nifty = text_of(mcp.call("browser_evaluate", {
        "function": """() => {
          document.querySelector('#market-glance [data-analyze=\"NIFTY\"]')?.click();
          return 'nifty-click';
        }"""
    }))
    time.sleep(10)
    nifty_after = text_of(mcp.call("browser_evaluate", {
        "function": """() => ({
          hero: document.getElementById('symbol-hero')?.innerText?.slice(0,300),
          symbol: (document.getElementById('symbol-hero')?.innerText||'').split('\\n')[0]
        })"""
    }))
    report["journeys"].append({"name": "nifty-click", "start": nifty, "after": nifty_after})
    print("NIFTY", nifty_after[:500])

    try:
        net = text_of(mcp.call("browser_network_requests", {}))
    except Exception as exc:  # noqa: BLE001 -- UAT probe
        net = str(exc)
    report["journeys"].append({"name": "network", "state": net[:2500]})
    print("NET", net[:600])

    (OUT / "mcp-journey-log.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("WROTE", OUT / "mcp-journey-log.json")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as exc:
        print("HTTP", exc.code, exc.read()[:500])
        raise
