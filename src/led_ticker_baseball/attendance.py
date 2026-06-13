"""MLB ballpark attendance widget — league superlatives + team mode.

Two modes (chosen by whether ``team`` is configured): league-wide daily
attendance superlatives (biggest/smallest crowd, fullest/emptiest park by
capacity %), or one tracked team's game (attendance + fill % + venue + weather).
All data is from the StatsAPI the plugin already uses; attendance exists only
once a game is Final (schedule has venue/capacity/state, the live feed has
weather, the boxscore carries the attendance string). Stateless: every refresh
re-derives, schedule-gated so off-hours ticks are cheap.
"""

import logging
import re
from dataclasses import dataclass
from datetime import date
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
    colors,
)

logger: logging.Logger = logging.getLogger(__name__)

_STAT_KEYS: tuple[str, ...] = (
    "biggest_crowd",
    "smallest_crowd",
    "fullest",
    "emptiest",
)

_STAT_LABELS: dict[str, str] = {
    "biggest_crowd": "Biggest crowd",
    "smallest_crowd": "Smallest crowd",
    "fullest": "Fullest",
    "emptiest": "Emptiest",
}

_DIGITS_RE: re.Pattern[str] = re.compile(r"\d")


def _parse_attendance(boxscore: dict[str, Any]) -> int | None:
    """Attendance from a boxscore's info[] 'Att' entry; None if absent/bad.

    The value is a formatted string like ``"19,587."`` — keep only digits.
    """
    for entry in boxscore.get("info", []):
        if entry.get("label") == "Att":
            digits = "".join(_DIGITS_RE.findall(entry.get("value", "")))
            return int(digits) if digits else None
    return None


def _fill_pct(attendance: int, capacity: int | None) -> int | None:
    """Rounded attendance/capacity percentage; None when capacity is 0/missing."""
    if not capacity:
        return None
    return round(attendance / capacity * 100)


def _format_weather(weather: dict[str, Any] | None) -> str | None:
    """'72° Clear, wind 5 mph, In From CF' from a feed weather dict.

    Returns None for empty/absent weather (future-day previews). Each piece is
    optional: temp+condition, or condition alone, etc.
    """
    if not weather:
        return None
    temp = weather.get("temp")
    condition = weather.get("condition")
    wind = weather.get("wind")
    head = f"{temp}° {condition}" if temp and condition else (condition or "")
    if not head:
        return None
    return f"{head}, wind {wind}" if wind else head


@dataclass(frozen=True)
class GameVenue:
    game_pk: int
    state: str  # abstractGameState: Preview / Live / Final
    game_number: int
    home_abbr: str
    away_abbr: str
    venue: str
    capacity: int  # 0 when the venue has no listed capacity


def _parse_schedule_games(data: dict[str, Any]) -> list[GameVenue]:
    """Flatten a hydrate=venue(fieldInfo),team schedule into GameVenue rows."""
    games: list[GameVenue] = []
    for date_entry in data.get("dates", []):
        for g in date_entry.get("games", []):
            teams = g.get("teams", {})
            home = teams.get("home", {}).get("team", {})
            away = teams.get("away", {}).get("team", {})
            venue = g.get("venue", {})
            games.append(
                GameVenue(
                    game_pk=g.get("gamePk", 0),
                    state=g.get("status", {}).get("abstractGameState", "Preview"),
                    game_number=g.get("gameNumber", 1),
                    home_abbr=home.get("abbreviation", ""),
                    away_abbr=away.get("abbreviation", ""),
                    venue=venue.get("name", ""),
                    capacity=venue.get("fieldInfo", {}).get("capacity", 0) or 0,
                )
            )
    return games


@dataclass(frozen=True)
class CrowdRecord:
    value: int  # raw attendance for crowd stats, percent for fullest/emptiest
    venue: str
    home_abbr: str
    is_pct: bool  # True → render value as "NN%"


def _derive_superlatives(
    pairs: list[tuple[GameVenue, int]], stats: list[str]
) -> dict[str, CrowdRecord]:
    """Best record per requested superlative over (game, attendance) pairs.

    Crowd stats use raw attendance over all pairs; fullest/emptiest use
    attendance/capacity over pairs with capacity > 0. Strict comparisons keep
    the first pair on ties (schedule order).
    """
    records: dict[str, CrowdRecord] = {}

    def consider(key: str, value: int, gv: GameVenue, *, lower: bool) -> None:
        cur = records.get(key)
        if cur is not None and (value >= cur.value if lower else value <= cur.value):
            return
        is_pct = key in ("fullest", "emptiest")
        records[key] = CrowdRecord(
            value=value, venue=gv.venue, home_abbr=gv.home_abbr, is_pct=is_pct
        )

    for gv, att in pairs:
        if "biggest_crowd" in stats:
            consider("biggest_crowd", att, gv, lower=False)
        if "smallest_crowd" in stats:
            consider("smallest_crowd", att, gv, lower=True)
        pct = _fill_pct(att, gv.capacity)
        if pct is not None:
            if "fullest" in stats:
                consider("fullest", pct, gv, lower=False)
            if "emptiest" in stats:
                consider("emptiest", pct, gv, lower=True)
    return records


@attrs.define
class MLBAttendanceMonitor:
    """Ballpark attendance — league superlatives, or one team's game."""

    session: aiohttp.ClientSession
    team: str = ""  # "" → league mode; else team mode
    stats: list[str] = attrs.field(factory=lambda: list(_STAT_KEYS))
    title: str = "Attendance"
    timezone: str = "America/New_York"
    padding: int = 6
    hold_time: float = 0.0
    bg_color: Color | None = attrs.field(default=None, kw_only=True)
    font_color: Color | ColorProvider | None = attrs.field(default=None, kw_only=True)
    font: Font = attrs.field(default=FONT_DEFAULT, kw_only=True)
    _tz: ZoneInfo | None = attrs.field(init=False, default=None)
    _team_id: int = attrs.field(init=False, default=0)
    # (local date, Final count) at the last successful derive; None until the
    # first success (and after any error/fallback).
    _last_derive: tuple[date, int] | None = attrs.field(init=False, default=None)
    feed_title: TickerMessage | SegmentMessage | None = attrs.field(
        init=False, default=None
    )
    feed_stories: list[TickerMessage | SegmentMessage] = attrs.field(
        init=False, factory=list
    )

    def _body_color(self) -> Color | ColorProvider:
        return self.font_color if self.font_color is not None else colors.RGB_WHITE

    def _plain_body_color(self) -> Color | ColorProvider:
        """Body-text color for per-segment use; a plain Color tints body text
        while callout segments keep their colors, a provider passes through and
        overrides every segment in core (same as the sibling widgets)."""
        if self.font_color is not None and not hasattr(self.font_color, "color_for"):
            return self.font_color
        return colors.RGB_WHITE

    def _set_title(self) -> None:
        self.feed_title = TickerMessage(
            self.title,
            font_color=self._body_color(),
            center=True,
            bg_color=self.bg_color,
        )

    def _should_skip(self, today: date, counts: tuple[int, int] | None) -> bool:
        """Skip refetch when nothing changed since the last successful derive.

        Re-derive when the gate fetch failed, no prior derive, the date rolled,
        any game is live, or the Final count moved.
        """
        if counts is None or self._last_derive is None:
            return False
        snap_day, snap_final = self._last_derive
        live, final = counts
        return snap_day == today and live == 0 and final == snap_final
