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
from typing import Any

logger: logging.Logger = logging.getLogger(__name__)

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
