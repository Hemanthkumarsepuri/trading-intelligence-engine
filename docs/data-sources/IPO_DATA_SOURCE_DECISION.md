# IPO Data Source Investigation

Status: **CORRECTED 2026-08-30 (same day, later re-check).** The conclusion
below this notice — "no authorized IPO endpoint exists" — was **wrong**. It
was reached from live-probing eight *guessed* endpoint paths, all of which
happened to be wrong guesses; the actual real endpoint (`GET /v2/ipos`) was
never tried. A fresh session was later given the exact real path directly and
re-probed it live rather than trusting the claim — and it is real, authorized,
and already working on this project's existing Upstox Analytics Token. See
**"CORRECTED FINDING"** below for the full re-investigation. The original STOP
section is kept beneath it, struck through in spirit but left intact, as an
honest record of how a real endpoint can be missed by guessing alone — the
same lesson Sprint 4 already learned once for `/v2/news`.

## CORRECTED FINDING (2026-08-30) — `/v2/ipos` is real, authorized, and working

Live-probed directly against the real API using the existing, already-
authorized Upstox Analytics Token — no new credential, no new vendor:

```
GET /v2/ipos                         -> 200, real data (10 open IPOs, no status param = "open")
GET /v2/ipos?status=upcoming          -> 200, 28 real upcoming IPOs (5 pages)
GET /v2/ipos?status=open              -> 200, 10 real open IPOs
GET /v2/ipos?status=closed            -> 200, 8 real closed IPOs
GET /v2/ipos?status=listed            -> 200, 95 real listed IPOs (5 pages)
GET /v2/ipos?status=all               -> 400 UDAPI1219 "Invalid IPO status. Allowed values: open, closed, listed, upcoming."
GET /v2/ipos/{id}                     -> 200, real per-IPO detail (timeline, registrar, investor categories, real listing_price once listed)
```

**Real pagination confirmed**: `meta_data.page` (`page_number`/`total_pages`/`records`/`total_records`), `page_number` query param.

**Fields confirmed real** (from actual response bodies, not documentation):
list view — `id`, `symbol`, `name`, `status`, `isin`, `issue_type` (`sme`/`regular`),
`issue_size`, `industry`, `minimum_price`/`maximum_price`, `bidding_start_date`/
`bidding_end_date`, `total_subscription` (a single aggregate multiple, as a
string). Detail view additionally: `face_value`, `tick_size`, `lot_size`,
`minimum_quantity`, `cut_off_price`, `listing_price` (real once `status ==
"listed"`, `null` otherwise), `listing_exchange`, `rhp_url`/`drhp_url` (real
PDF links to the actual offer document), `timeline` (`pre_apply_start_date`
through `mandate_end_date`, 8 real dates), `registrar_info` (name/email/
contact/website), `investors` (a list of real reservation CATEGORY CODES —
observed values `IND` (retail/individual), `EMP` (employee), `HNI`, and `SHA`
(shareholder) — confirmed present on a real currently-listed IPO with a
shareholder quota).

**Real companies verified present, live, right now** — including the exact
three the product's own examples named: **Tempsens Instruments (India) IPO**
(status: listed, real `listing_price=634.0` against a ₹285–300 band),
**Hy-Tech Engineers IPO** (status: closed), **Symbiotec Pharmalab IPO**
(status: closed). Also confirmed: **no "Jio"- or "Reliance"-named IPO exists
anywhere in the real feed** — checked exhaustively across all 4 statuses, all
pages (141 total real records) — so any question about a Jio Platforms
shareholder quota must currently be answered "no such IPO exists in the
authorized data source," not fabricated.

**Fields confirmed genuinely NOT present in this endpoint** — i.e., still
correctly out of scope: **category-wise subscription** (QIB/NII/retail
broken out separately — only a single aggregate `total_subscription` exists),
**GMP** (grey market premium — this endpoint has none, consistent with GMP
being inherently unofficial), **valid application counts**, **exact
shareholder-quota reserved-share counts / record dates / minimum holding**
(the `investors` list confirms a category's *existence*, never its terms —
those remain only in the real `rhp_url`/`drhp_url` PDF), and **business
fundamentals** (revenue/profit/valuation — not this endpoint's job).

**Conclusion**: Upstox is now the primary, official, authorized IPO
lifecycle/identity/timeline/registrar/listing-price data source for this
project — `UpstoxProvider.get_ipos()`/`get_ipo_detail()`. GMP remains
unofficial and is NOT provided by this or any other source this project
holds — the input-driven GMP layer built in the prior milestone is unchanged
and still correct. Category-wise subscription and shareholder-quota exact
terms remain input-driven (caller-supplied) for the same reason.

---

## ORIGINAL FINDING (2026-08-30, superseded above) — kept as an honest record

Status: **Investigated 2026-08-30 (same day, earlier in the session). No legitimate, authorized, non-scraping, free data
source for GMP / subscription / allotment / IPO-calendar data was found.** This
document follows the exact same "STOP and report" discipline this project already
applied to news/corporate-events in `PROVIDER_DECISION.md` — not silently worked
around with a scraper or fabricated data. **This conclusion was wrong for IPO
calendar/identity data — see CORRECTED FINDING above.** It remains correct for
GMP specifically.

## What was actually checked

**The already-authorized Upstox Analytics Token** (the only credential this
project holds) — live-probed directly against the real API on 2026-08-30, the
same "try plausible endpoint guesses, read the real error code" methodology
that discovered `/v2/news` in Sprint 4:

```
GET /v2/ipo              -> 404 UDAPI100060 "Resource not Found"
GET /v2/ipo/list         -> 404 UDAPI100060
GET /v2/market-info/ipo  -> 404 UDAPI100060
GET /v2/ipo/allotment    -> 404 UDAPI100060
GET /v2/ipo/gmp          -> 404 UDAPI100060
GET /v2/ipo/subscription -> 404 UDAPI100060
GET /v3/ipo              -> 404 UDAPI100060
GET /v2/ipo/upcoming     -> 404 UDAPI100060
```

Every guess returned the same structured "resource not found" error — the same
signature this project has always treated as "this endpoint genuinely does not
exist" (as opposed to Sprint 4's `/v2/news` discovery, where a guess returned a
real `400` naming its own required parameters — the tell that an endpoint
exists but was called wrong). **Conclusion: the Upstox Analytics Token has no
IPO-related endpoint of any kind.** (Upstox's own *consumer trading app* lets
retail users apply to IPOs, but that is a different, unauthenticated-to-this-
project product surface, not an API this codebase holds a credential for —
integrating it would be a new, separate provider decision, not "reusing Upstox".)

**Reviewed against this project's own architecture**: `app/data/providers/`
contains no IPO-capable provider; `docs/data-sources/PROVIDERS.md`/
`PROVIDER_DECISION.md` never mention IPO data in any prior milestone.

## The specific gaps, evaluated individually

| Data need | A legitimate, free, authorized API source? | Verdict |
|---|---|---|
| IPO calendar (open/upcoming/closed dates, price band, lot size) | NSE/BSE publish this on their own websites; no public REST API found. SEBI publishes RHP/DRHP as PDFs on sebi.gov.in — real public documents, but no structured API, and mass-fetching+parsing financial figures out of arbitrary PDFs is a large, separate extraction project, not a data-source integration. | **Not built.** No API-based legitimate source found this session. |
| GMP (grey market premium) | **Structurally unofficial by definition** — no exchange, regulator, or company publishes it. It exists only on community tracker sites (InvestorGain, IPO Watch, Chittorgarh, and similar) with no public API. Fetching it requires either scraping (this project's policy forbids it) or a paid vendor (requires an explicit owner decision this session was not given). | **Not built.** Cannot be legitimately automated today. |
| Subscription / category-wise bidding data | NSE/BSE publish live bidding data on their own websites; no public REST API found without scraping. | **Not built.** |
| Allotment results | Registrars (Link Intime, KFin, Bigshare, etc.) publish per-PAN lookup tools on their own sites; no public bulk API found. | **Not built.** |
| Shareholder-quota eligibility / record dates | Only ever confirmed in the official RHP/offer document for that specific IPO — no API; would require reading the actual document for that specific issue. | **Not built as an automated feed** — see below for what IS built. |

## Why this session does not fabricate a workaround

This project's non-negotiable rule, applied consistently since Sprint 0: **no
fabricated data, no guessed provider, no scraping, no paid vendor added
unilaterally.** GMP, subscription counts, allotment odds, and shareholder-quota
status for real, named securities are exactly the kind of number a user could
act on with real money — inventing a plausible-looking figure here would be
actively harmful, not merely a scope shortcut. The prompt's own worked examples
(TEMPSENS, HY-TECH ENGINEERS, SYMBIOTEC, JIO PLATFORMS) are **not addressed
with any current status, GMP, or eligibility claim in this milestone** — this
session has no live, verified information about any of them, and this
document does not use its own training-data recall as if it were a real-time
verified source. That would be exactly the failure mode this project exists to
prevent.

## What this milestone builds instead

A complete, honest, **input-driven** IPO Intelligence layer: real domain math
(SEBI's actual documented retail-lottery allotment mechanics, GMP-range
aggregation, freshness classification, listing-range arithmetic), a real query
parser that routes IPO-shaped queries away from the options parser, and a real
orchestration/API layer — all operating on data the CALLER supplies (a human
operator reading a GMP tracker/NSE bidding page and typing the numbers in, or a
future provider once one is authorized), never data this system fetched or
invented itself. Every function that would need live data honestly returns
`NOT_AVAILABLE`/`INSUFFICIENT_DATA` when the caller doesn't supply it — never a
guess.

## Path to real automation for GMP specifically (owner decision still required)

IPO calendar/identity/timeline/registrar/listing-price is now real and live
(see CORRECTED FINDING above) — no further decision needed there. **GMP is
the one piece that remains genuinely unavailable from any authorized source**,
and two options exist, neither exercised this session:
1. **A paid GMP data vendor** (e.g. an Indian fintech data API that licenses
   this specific data) — would need the owner to choose and pay for one.
2. **A legitimate scraping exemption** for named GMP-tracker sites — this
   project's policy currently forbids scraping outright; lifting it would be
   a deliberate policy change, not an engineering call this session can make.

Until one of those is decided, GMP in this product remains a **calculator
over data the user supplies**, layered on top of now-real IPO
calendar/identity data for everything else.
