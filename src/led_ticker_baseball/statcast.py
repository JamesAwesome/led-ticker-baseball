"""MLB league-wide Statcast superlatives widget.

Derives the day's longest home run, hardest-hit ball, and fastest/slowest
pitch from Baseball Savant's day CSV — an undocumented website endpoint, so
requests carry a User-Agent and the default refresh is a polite 30 minutes,
gated on the (tiny) StatsAPI day schedule so off-hours refreshes skip the
3 MB pull. Stateless: every refresh re-derives from the full day so far.
"""

import logging
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
    "longest_hr",
    "hardest_hit",
    "fastest_pitch",
    "slowest_pitch",
)

_STAT_LABELS: dict[str, str] = {
    "longest_hr": "Longest HR",
    "hardest_hit": "Hardest hit",
    "fastest_pitch": "Fastest pitch",
    "slowest_pitch": "Slowest pitch",
}


def _to_float(row: dict[str, Any], key: str) -> float | None:
    """Float column value; None when missing, blank, or malformed.

    A ``"0"`` string is a valid reading and returns ``0.0``. (Savant CSV
    values are always strings via DictReader, so the falsy-zero edge of the
    ``or ""`` guard only applies to a literal int/float 0, which CSV rows
    never carry.)
    """
    try:
        return float(row.get(key) or "")
    except ValueError:
        return None


def _to_id(row: dict[str, Any], key: str) -> int:
    """Person-ID column value; 0 when missing or malformed."""
    try:
        return int(row.get(key) or 0)
    except ValueError:
        return 0


def _row_team(row: dict[str, Any], who: str) -> str:
    """Team abbreviation for ``who`` ('batter' / 'pitcher') on this row.

    Savant rows carry only home_team/away_team; ``inning_topbot`` says who is
    batting (Top = away batting). The batter's team follows topbot; the
    pitcher's team is the other one.
    """
    batting_away = row.get("inning_topbot") == "Top"
    if who == "batter":
        team = row.get("away_team") if batting_away else row.get("home_team")
    else:
        team = row.get("home_team") if batting_away else row.get("away_team")
    return team or ""


def _format_value(key: str, value: float) -> str:
    """'463 ft' for the HR distance, '101.8 mph' for speeds."""
    if key == "longest_hr":
        return f"{round(value)} ft"
    return f"{value:.1f} mph"


@dataclass(frozen=True)
class StatRecord:
    value: float
    person_id: int
    team_abbr: str
    pitch_name: str = ""


def _derive_records(
    rows: list[dict[str, Any]], stats: list[str]
) -> dict[str, StatRecord]:
    """One pass over Savant rows → best record per requested stat key.

    Strict comparisons keep the first row on ties (CSV order). Rows missing
    the relevant value are skipped.
    """
    records: dict[str, StatRecord] = {}

    def consider(
        key: str,
        r: dict[str, Any],
        value: float | None,
        who: str,
        *,
        lower: bool = False,
    ) -> None:
        if value is None:
            return
        cur = records.get(key)
        if cur is not None and (value >= cur.value if lower else value <= cur.value):
            return
        records[key] = StatRecord(
            value=value,
            person_id=_to_id(r, who),
            team_abbr=_row_team(r, who),
            pitch_name=r.get("pitch_name") or "",
        )

    for r in rows:
        if "longest_hr" in stats and r.get("events") == "home_run":
            consider("longest_hr", r, _to_float(r, "hit_distance_sc"), "batter")
        if "hardest_hit" in stats and r.get("description") == "hit_into_play":
            consider("hardest_hit", r, _to_float(r, "launch_speed"), "batter")
        if "fastest_pitch" in stats:
            consider("fastest_pitch", r, _to_float(r, "release_speed"), "pitcher")
        if "slowest_pitch" in stats:
            consider(
                "slowest_pitch", r, _to_float(r, "release_speed"), "pitcher", lower=True
            )
    return records


@attrs.define
class MLBStatcastMonitor:
    """League-wide daily Statcast superlatives."""

    session: aiohttp.ClientSession
    stats: list[str] = attrs.field(factory=lambda: list(_STAT_KEYS))
    title: str = "Statcast"
    timezone: str = "America/New_York"
    padding: int = 6
    hold_time: float = 0.0
    bg_color: Color | None = attrs.field(default=None, kw_only=True)
    font_color: Color | ColorProvider | None = attrs.field(default=None, kw_only=True)
    font: Font = attrs.field(default=FONT_DEFAULT, kw_only=True)
    _tz: ZoneInfo | None = attrs.field(init=False, default=None)
    # (local date, Final-game count) at the last successful derive; None
    # means no successful derive yet (first run, or the last update ended
    # in an error/fallback state).
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
        """Body-text color for per-segment use.

        A plain-Color ``font_color`` tints body text while callout segments
        (day label, amber value, team abbr) keep their colors. Providers
        (``color_for``) can't color a single segment; they pass through
        ``font_color=`` on the message instead, which overrides every
        segment in core — same as the sibling widgets.
        """
        if self.font_color is not None and not hasattr(self.font_color, "color_for"):
            return self.font_color
        return colors.RGB_WHITE

    def _set_title(self) -> None:
        """League-wide title; no team color (there is no team)."""
        self.feed_title = TickerMessage(
            self.title,
            font_color=self._body_color(),
            center=True,
            bg_color=self.bg_color,
        )

    def _should_skip(self, today: date, counts: tuple[int, int] | None) -> bool:
        """Skip the 3 MB pull when nothing changed since the last derive.

        Re-derive when: the gate fetch failed (fail open), no successful
        derive exists yet, the local date rolled over, any game is live, or
        the Final count moved.
        """
        if counts is None or self._last_derive is None:
            return False
        snap_day, snap_final = self._last_derive
        live, final = counts
        return snap_day == today and live == 0 and final == snap_final
