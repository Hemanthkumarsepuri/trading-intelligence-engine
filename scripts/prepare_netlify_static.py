"""Copy the static dashboard and inject TIRE_API_BASE for Netlify.

Never writes secrets. TIRE_API_BASE must be an HTTPS origin with no path.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "app" / "api" / "static"
DEST = ROOT / "dist-netlify"
MARKER = '<meta name="api-base" content="">'


def main() -> None:
    base = (os.environ.get("TIRE_API_BASE") or "").strip().rstrip("/")
    if base.startswith("http://") and "localhost" not in base and "127.0.0.1" not in base:
        raise SystemExit("TIRE_API_BASE must be HTTPS in production")
    if "localhost:8931" in base:
        raise SystemExit("TIRE_API_BASE must not point at Playwright MCP")
    dest = DEST
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(SRC, dest, ignore=shutil.ignore_patterns("DEPLOYMENT.md"))
    html_path = dest / "index.html"
    html = html_path.read_text(encoding="utf-8")
    if MARKER not in html:
        raise SystemExit("api-base marker missing from index.html")
    html_path.write_text(html.replace(MARKER, f'<meta name="api-base" content="{base}">'), encoding="utf-8")
    print("wrote", html_path, "api-base=", base or "(same-origin empty)")


if __name__ == "__main__":
    main()
