"""Persistent-session client for Playwright MCP over HTTP.

Usage: python scripts/playwright_mcp_session.py tools
Never prints secrets. Targets public HTTPS URLs only.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

MCP = "http://localhost:8931/mcp"


def _parse_sse(raw: bytes) -> dict:
    text = raw.decode("utf-8", "replace")
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    return json.loads(text)


class McpSession:
    def __init__(self, url: str = MCP) -> None:
        self.url = url
        self.session_id: str | None = None
        self._id = 0

    def call(self, method: str, params: dict | None = None, *, notification: bool = False) -> dict:
        self._id += 1
        payload: dict[str, object] = {"jsonrpc": "2.0", "method": method}
        if not notification:
            payload["id"] = self._id
        if params is not None:
            payload["params"] = params
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        req = urllib.request.Request(self.url, data=json.dumps(payload).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                sid = resp.headers.get("Mcp-Session-Id") or resp.headers.get("mcp-session-id")
                if sid:
                    self.session_id = sid
                body = resp.read()
        except urllib.error.HTTPError as exc:
            body = exc.read()
            raise RuntimeError(f"{method} HTTP {exc.code}: {body[:500]!r}") from exc
        if notification or not body:
            return {}
        parsed = _parse_sse(body)
        if "error" in parsed:
            raise RuntimeError(f"{method} error: {parsed['error']}")
        return parsed.get("result") or parsed

    def initialize(self) -> dict:
        result = self.call(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "tire-release", "version": "1"},
            },
        )
        self.call("notifications/initialized", {}, notification=True)
        return result


def _tool(sess: McpSession, name: str, arguments: dict[str, object]) -> object:
    return sess.call("tools/call", {"name": name, "arguments": arguments})


def first_paint(sess: McpSession, url: str, width: int, height: int, shot: str) -> None:
    _tool(sess, "browser_resize", {"width": width, "height": height})
    _tool(sess, "browser_navigate", {"url": "about:blank"})
    _tool(sess, "browser_console_messages", {"level": "warning", "all": True})
    _tool(sess, "browser_navigate", {"url": url})
    _tool(sess, "browser_snapshot", {"depth": 4})
    ev = _tool(
        sess,
        "browser_evaluate",
        {
            "function": (
                "() => ({origin: location.origin, apiBase: "
                "document.querySelector('meta[name=api-base]')?.content || '', "
                "title: document.title, tire: /\\bTIRE\\b/.test(document.body.innerText)})"
            )
        },
    )
    print("evaluate", width, "x", height, str(ev)[:500])
    _tool(sess, "browser_take_screenshot", {"filename": shot, "type": "png"})
    cons = _tool(sess, "browser_console_messages", {"level": "warning", "all": True})
    net = _tool(sess, "browser_network_requests", {"static": True})
    print("console", str(cons)[:1000])
    print("network", str(net)[:1500])


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "tools"
    sess = McpSession()
    info = sess.initialize()
    print("initialized", info.get("serverInfo"), "session", bool(sess.session_id))
    tools = sess.call("tools/list")
    names = [t.get("name") for t in (tools.get("tools") or [])]
    print("tools", names)
    required = {
        "browser_navigate",
        "browser_resize",
        "browser_snapshot",
        "browser_evaluate",
        "browser_take_screenshot",
        "browser_console_messages",
        "browser_network_requests",
    }
    missing = sorted(required - set(names))
    if missing:
        raise SystemExit(f"missing MCP tools: {missing}")
    if cmd == "tools":
        return
    if cmd == "first-paint":
        url = sys.argv[2] if len(sys.argv) > 2 else "https://api-production-983e.up.railway.app/"
        first_paint(sess, url, 1280, 800, "docs/uat/phase-4/mcp-firstpaint-1280x800.png")
        first_paint(sess, url, 1440, 900, "docs/uat/phase-4/mcp-firstpaint-1440x900.png")
        first_paint(sess, url, 390, 844, "docs/uat/phase-4/mcp-firstpaint-390x844.png")
        first_paint(sess, url, 360, 800, "docs/uat/phase-4/mcp-firstpaint-360x800.png")
        return
    raise SystemExit(f"unknown command {cmd}")


if __name__ == "__main__":
    main()
