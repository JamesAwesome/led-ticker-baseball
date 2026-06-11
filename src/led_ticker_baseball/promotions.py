"""MLB home-game promotions widget using the free MLB Stats API.

Data comes from the schedule endpoint's ``promotions`` hydration — giveaways
and theme nights attached to each home game (e.g. the Blue Jays' "Loonie Dogs
Night"). The API has no live counter data; this widget shows what's on, not
how many hot dogs were eaten.
"""

import contextlib
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp
import attrs
from led_ticker.plugin import (
    FONT_DEFAULT,
    Color,
    ColorProvider,
    Font,
    SegmentMessage,
    TickerMessage,
)

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


@dataclass
class GamePromos:
    game_date: date  # local calendar date of the home game
    promos: list[str] = field(default_factory=list)


@attrs.define
class MLBPromotionsMonitor:
    """Upcoming home-game promotions (giveaways / theme nights) for one team."""

    session: aiohttp.ClientSession
    team: str
    title: str = ""
    timezone: str = "America/New_York"
    lookahead_days: int = 14
    highlight: list[str] = attrs.field(factory=list)
    filter: list[str] = attrs.field(factory=list)
    limit: int = 0
    padding: int = 6
    hold_time: float = 0.0
    bg_color: Color | None = attrs.field(default=None, kw_only=True)
    font_color: Color | ColorProvider | None = attrs.field(default=None, kw_only=True)
    font: Font = attrs.field(default=FONT_DEFAULT, kw_only=True)
    _team_id: int = attrs.field(init=False, default=0)
    _tz: ZoneInfo | None = attrs.field(init=False, default=None)
    feed_title: TickerMessage | SegmentMessage | None = attrs.field(
        init=False, default=None
    )
    feed_stories: list[TickerMessage | SegmentMessage] = attrs.field(
        init=False, factory=list
    )

    def _parse_home_games(
        self, data: dict[str, Any], tz: ZoneInfo
    ) -> tuple[list[GamePromos], bool]:
        """Per-date home-game promo lists from a schedule response.

        Returns (games sorted by date, whether the response had ANY games) —
        the flag distinguishes a road trip from the offseason in the
        fallback path. Doubleheader promos merge into one date entry.
        """
        by_date: dict[date, list[str]] = {}
        had_games = False
        for date_entry in data.get("dates", []):
            for g in date_entry.get("games", []):
                had_games = True
                home = g.get("teams", {}).get("home", {}).get("team", {})
                if home.get("id") != self._team_id:
                    continue
                d = _game_local_date(g, tz)
                if d is None:
                    continue
                names = [
                    _clean_promo_name(p["name"])
                    for p in g.get("promotions", [])
                    if p and p.get("name")
                ]
                by_date.setdefault(d, []).extend(names)
        games = [
            GamePromos(game_date=d, promos=_dedupe_promos(names))
            for d, names in sorted(by_date.items())
        ]
        return games, had_games
