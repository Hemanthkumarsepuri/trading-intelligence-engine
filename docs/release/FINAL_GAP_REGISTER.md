# TIRE final gap register

Date: 2026-09-20 (IST). Product freeze: `d14a13e` on `phase-3-historical-validation`.
Public origin: `https://api-production-983e.up.railway.app`.

The process exit `4294967295` was an intentional shutdown. It is not a gap.

| ID | Gap | Class | Status |
|----|-----|-------|--------|
| G1 | Railway `/data` JSONL survival | P0 | PASS — history `161e6e55…` and journal `7538e7ec…` survived restart |
| G2 | Discover in-memory jobs die; JSONL survives | P1 | VERIFIED — job `0947b6a8396a` RUNNING 15/198 then `NONE` after restart |
| G3 | Full F&O scan on a valid trading session | P0 | FAIL — Sunday CLOSED; scan started (`F&O_UNIVERSE` total 198) then interrupted for G2. Not completed. Not fabricated. |
| G4 | Production replay dataset | P1 | VERIFIED DECISION — no local dataset to copy; keep honest unavailable; live journal works |
| G5 | First-paint console from t=0 | P1 | PASS — Playwright MCP, 4 viewports, 0 errors/warnings |
| G6 | Playwright MCP persistent session | P1 | PASS |
| G7 | Cold restart | P0 | PASS |
| G8 | Secret audit | P0 | PASS on Railway/git/frontend; Netlify env N/A |
| G9 | Production localhost | P0 | PASS |
| G10 | dist-netlify `TIRE_API_BASE` | P1 | PASS locally |
| G11 | Netlify HTTPS publish | P0 | FAIL — authorize page requires human login |
| G12 | CORS exact Netlify origin | P0 | FAIL (depends G11) |
| G13 | Split network audit | P0 | FAIL (depends G11) |
| G14 | Public MCP UAT on Netlify | P0 | FAIL (depends G11) |
| G15 | Viewports 1280/1440/390/360 | P1 | PASS on Railway |
| G16 | Deployment wiring uncommitted | P1 | closed by release commit if git is clean after |
| G17 | Rollback | P2 | Restart tested; older-image rollback documented not executed |
| G18 | Qwen fail-open | P1 | PASS `QWEN_ENABLED=false` |
| G19 | Health `data_root` | P2 | Code added; live process may still omit until next `railway up` |
| G20 | Replay dataset missing locally | P2 | accepted — do not invent |
| G21 | Railway SSH | P2 | key registered; interactive SSH listing not completed |
| G22 | Dirty tree vs freeze | P1 | closed by commit |

## Unfreeze

Blocked by G3 and G11. FINAL GREEN / UNFROZEN is not allowed.
