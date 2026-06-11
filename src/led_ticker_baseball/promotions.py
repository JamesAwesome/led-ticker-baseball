"""MLB home-game promotions widget using the free MLB Stats API.

Data comes from the schedule endpoint's ``promotions`` hydration — giveaways
and theme nights attached to each home game (e.g. the Blue Jays' "Loonie Dogs
Night"). The API has no live counter data; this widget shows what's on, not
how many hot dogs were eaten.
"""

import contextlib
import logging
import re
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

logger: logging.Logger = logging.getLogger(__name__)

# "Loonie Dogs Night presented by Schneiders" → "Loonie Dogs Night"
_SPONSOR_RE: re.Pattern[str] = re.compile(
    r"\s+(?:presented by|pres\. by)\s+.*$", re.IGNORECASE
)


def _clean_promo_name(name: str) -> str:
    """Strip sponsor tails: 'X presented by Y' / 'X pres. by Y' → 'X'."""
    return _SPONSOR_RE.sub("", name).strip()


def _dedupe_promos(names: list[str]) -> list[str]:
    """Collapse duplicate promo names, keeping feed order.

    Exact duplicates (casefolded) are dropped; when one name is a prefix of
    another (the feed lists both "Dylan Cease Bobblehead Giveaway Night" and
    "Dylan Cease Bobblehead Giveaway"), the shorter name wins. Pairwise only:
    three-way prefix chains within one game's promo list aren't fully
    collapsed — the feed has never produced one.
    """
    kept: list[str] = []
    for name in names:
        cf = name.casefold()
        dominated = False
        for i, other in enumerate(kept):
            ocf = other.casefold()
            if cf.startswith(ocf):
                dominated = True  # a shorter-or-equal name is already kept
                break
            if ocf.startswith(cf):
                kept[i] = name  # new name is shorter; it wins
                dominated = True
                break
        if not dominated:
            kept.append(name)
    return kept


def _match_any(name: str, keywords: list[str]) -> bool:
    """Case-insensitive substring match against any keyword."""
    n = name.casefold()
    return any(k.casefold() in n for k in keywords)


def _game_local_date(g: dict[str, Any], tz: ZoneInfo) -> date | None:
    """Local calendar date of a schedule game: officialDate, else gameDate."""
    official = g.get("officialDate")
    if official:
        with contextlib.suppress(ValueError, TypeError):
            return date.fromisoformat(official)
    game_date = g.get("gameDate")
    if game_date:
        with contextlib.suppress(ValueError, TypeError):
            return datetime.fromisoformat(game_date).astimezone(tz).date()
    return None
