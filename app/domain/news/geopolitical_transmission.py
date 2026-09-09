"""Sprint 7B, Objectives 2/3 -- geopolitical/macro TRANSMISSION analysis.

Deliberately NOT "geopolitical tension = bearish." For a real news item
already classified GEOPOLITICAL or MACRO (`event_classification.
classify_news_event()`, reused verbatim, never re-classified), this
module identifies which real TRANSMISSION CHANNEL(S) the headline text
implicates (a real, deterministic keyword match, same discipline as
`event_classification.py`), and -- ONLY where this system already
fetches a real corresponding macro series (crude oil, USD/INR, India
VIX; see `global_context.py`) -- reports that series' own REAL, already-
computed day-change as the "affected market variable" direction. This is
never inferred from the headline's own tone; it is a real, already-
observed macro fact linked to a real, keyword-matched channel.

No current geopolitical event is hardcoded anywhere in this module --
every note is built from whatever REAL news items and REAL macro moves
a given run actually has.

=== SECTOR/COMPANY EXPOSURE IN THIS MODULE ===
Equity research cards use the official NSE Nifty 500 industry map.
This news-transmission module still does **not** map a headline to a
company's official sector. It does not infer sector from a company name.
Every `sector_exposure`/`company_exposure` field here is therefore
honestly `"UNKNOWN"` -- never guessed from a symbol. The field exists so
a future authorized mapping has somewhere to report into without a schema
change.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from app.domain.news.event_classification import NewsEventCategory, classify_news_event


class TransmissionChannel(str, Enum):
    OIL = "OIL"
    CURRENCY = "CURRENCY"
    INFLATION = "INFLATION"
    INTEREST_RATES = "INTEREST_RATES"
    FOREIGN_FLOWS = "FOREIGN_FLOWS"
    SUPPLY_CHAIN = "SUPPLY_CHAIN"
    DEFENCE = "DEFENCE"
    EXPORTS = "EXPORTS"
    IMPORT_COST = "IMPORT_COST"
    GLOBAL_RISK = "GLOBAL_RISK"
    COMMODITIES = "COMMODITIES"
    SHIPPING = "SHIPPING"
    POLICY = "POLICY"
    UNKNOWN = "UNKNOWN"


_CHANNEL_KEYWORDS: dict[TransmissionChannel, tuple[str, ...]] = {
    TransmissionChannel.OIL: ("crude", "oil price", "opec", "oil supply"),
    TransmissionChannel.CURRENCY: ("rupee", "currency", "usd/inr", "forex", "inr weakens", "inr strengthens"),
    TransmissionChannel.INFLATION: ("inflation", "cpi", "wpi", "price rise"),
    TransmissionChannel.INTEREST_RATES: ("interest rate", "repo rate", "rbi policy", "fed rate", "monetary policy"),
    TransmissionChannel.FOREIGN_FLOWS: ("fii", "fpi", "foreign investor", "foreign outflow", "foreign inflow"),
    TransmissionChannel.SUPPLY_CHAIN: ("supply chain", "shortage", "chip shortage", "semiconductor supply"),
    TransmissionChannel.DEFENCE: ("defence", "military", "missile", "border tension"),
    TransmissionChannel.EXPORTS: ("export", "exporters"),
    TransmissionChannel.IMPORT_COST: ("import bill", "import cost", "import duty"),
    TransmissionChannel.GLOBAL_RISK: ("geopolitical", "tension", "conflict", "sanctions", "middle east", "war"),
    TransmissionChannel.COMMODITIES: ("commodity", "metal prices", "gold price"),
    TransmissionChannel.SHIPPING: ("shipping", "red sea", "suez", "freight"),
    TransmissionChannel.POLICY: ("government policy", "budget", "tariff", "regulation"),
}

# Only channels where this system already fetches a real, corresponding
# macro series (see global_context.py) get a real linked market variable
# -- every other channel honestly has none wired up.
_CHANNEL_TO_MARKET_VARIABLE = {
    TransmissionChannel.OIL: "MCX Crude Oil",
    TransmissionChannel.CURRENCY: "USD/INR",
    TransmissionChannel.GLOBAL_RISK: "India VIX",
}


def classify_transmission_channels(title: str, summary: str | None) -> list[TransmissionChannel]:
    """Real, deterministic keyword match -- never a guess. `[UNKNOWN]`
    (never an empty list) when nothing matches, so a caller always has a
    real value to report rather than silently omitting the item."""
    text = f"{title} {summary or ''}".lower()
    matched = [ch for ch, kws in _CHANNEL_KEYWORDS.items() if any(kw in text for kw in kws)]
    return matched or [TransmissionChannel.UNKNOWN]


@dataclass(frozen=True)
class TransmissionNote:
    event_title: str
    category: NewsEventCategory
    channels: list[TransmissionChannel]
    affected_market_variable: str | None  # real label (e.g. "MCX Crude Oil") or None if no real series is wired for any matched channel
    market_variable_day_change_pct: Decimal | None  # the REAL, already-fetched day-change of that variable, never inferred from headline tone
    # POSITIVE/NEGATIVE describe the REAL market variable's OWN raw move
    # (e.g. crude UP = POSITIVE) -- deliberately NOT an economic value
    # judgment about whether that move is "good" or "bad" for equities
    # (that would require a directional interpretation this system does
    # not fabricate). MIXED = a real but not-meaningful move; UNKNOWN =
    # no real series wired for the matched channel, or the value itself
    # is unavailable.
    direction: str
    sector_exposure: str  # always "UNKNOWN" today -- see module docstring
    company_exposure: str  # always "UNKNOWN" today -- see module docstring


def build_transmission_note(
    *, title: str, summary: str | None, category: NewsEventCategory,
    crude_day_change_pct: Decimal | None, usdinr_day_change_pct: Decimal | None, vix_day_change_pct: Decimal | None,
    meaningful_move_pct: Decimal,
) -> TransmissionNote | None:
    """`None` when the item isn't GEOPOLITICAL/MACRO to begin with -- this
    module never runs transmission analysis over an EARNINGS/ORDER/etc.
    headline that has no real macro-transmission story to tell.
    """
    if category not in (NewsEventCategory.GEOPOLITICAL, NewsEventCategory.MACRO):
        return None

    channels = classify_transmission_channels(title, summary)
    real_series = {
        TransmissionChannel.OIL: crude_day_change_pct, TransmissionChannel.CURRENCY: usdinr_day_change_pct,
        TransmissionChannel.GLOBAL_RISK: vix_day_change_pct,
    }
    matched_channel = next((ch for ch in channels if ch in _CHANNEL_TO_MARKET_VARIABLE), None)
    if matched_channel is None:
        return TransmissionNote(
            event_title=title, category=category, channels=channels, affected_market_variable=None,
            market_variable_day_change_pct=None, direction="UNKNOWN",
            sector_exposure="UNKNOWN", company_exposure="UNKNOWN",
        )

    change = real_series[matched_channel]
    variable_label = _CHANNEL_TO_MARKET_VARIABLE[matched_channel]
    if change is None:
        direction = "UNKNOWN"
    elif abs(change) < meaningful_move_pct:
        direction = "MIXED"
    elif change > 0:
        direction = "POSITIVE"
    else:
        direction = "NEGATIVE"

    return TransmissionNote(
        event_title=title, category=category, channels=channels, affected_market_variable=variable_label,
        market_variable_day_change_pct=change, direction=direction,
        sector_exposure="UNKNOWN", company_exposure="UNKNOWN",
    )


def classify_news_event_for_transmission(title: str, summary: str | None) -> NewsEventCategory:
    """Thin re-export so callers building transmission notes don't need
    a second import for the same real classification -- never a second
    classifier."""
    return classify_news_event(title, summary)
