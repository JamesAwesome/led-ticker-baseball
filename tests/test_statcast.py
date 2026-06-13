"""Tests for the MLB league-wide Statcast superlatives widget."""

import datetime as dt
import unittest.mock as mock
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")


def row(**kwargs):
    """A Savant-shaped row dict; only the columns the code reads."""
    base = {
        "events": "",
        "description": "",
        "release_speed": "",
        "launch_speed": "",
        "hit_distance_sc": "",
        "pitch_name": "",
        "batter": "1",
        "pitcher": "2",
        "home_team": "PHI",
        "away_team": "TOR",
        "inning_topbot": "Top",
    }
    base.update({k: str(v) for k, v in kwargs.items()})
    return base


def hr(dist, batter=10, **kwargs):
    return row(
        events="home_run",
        description="hit_into_play",
        hit_distance_sc=dist,
        launch_speed=100.0,
        release_speed=95.0,
        batter=batter,
        **kwargs,
    )


class TestRowHelpers:
    def test_to_float_parses(self):
        from led_ticker_baseball.statcast import _to_float

        assert _to_float({"x": "101.8"}, "x") == 101.8
        assert _to_float({"x": "0"}, "x") == 0.0

    def test_to_float_blank_and_garbage_are_none(self):
        from led_ticker_baseball.statcast import _to_float

        assert _to_float({"x": ""}, "x") is None
        assert _to_float({"x": "null"}, "x") is None
        assert _to_float({}, "x") is None

    def test_to_id(self):
        from led_ticker_baseball.statcast import _to_id

        assert _to_id({"batter": "660271"}, "batter") == 660271
        assert _to_id({"batter": ""}, "batter") == 0
        assert _to_id({}, "batter") == 0

    def test_row_team_batter_top_is_away(self):
        from led_ticker_baseball.statcast import _row_team

        r = row(inning_topbot="Top")
        assert _row_team(r, "batter") == "TOR"
        assert _row_team(r, "pitcher") == "PHI"

    def test_row_team_batter_bottom_is_home(self):
        from led_ticker_baseball.statcast import _row_team

        r = row(inning_topbot="Bot")
        assert _row_team(r, "batter") == "PHI"
        assert _row_team(r, "pitcher") == "TOR"

    def test_format_value(self):
        from led_ticker_baseball.statcast import _format_value

        assert _format_value("longest_hr", 463.0) == "463 ft"
        assert _format_value("fastest_pitch", 101.84) == "101.8 mph"
        assert _format_value("hardest_hit", 113.4) == "113.4 mph"


class TestDeriveRecords:
    def _derive(self, rows, stats=None):
        from led_ticker_baseball.statcast import _STAT_KEYS, _derive_records

        return _derive_records(rows, list(stats or _STAT_KEYS))

    def test_longest_hr_takes_max_distance(self):
        records = self._derive([hr(415, batter=10), hr(463, batter=11)])
        rec = records["longest_hr"]
        assert rec.value == 463.0
        assert rec.person_id == 11

    def test_hardest_hit_only_counts_balls_in_play(self):
        rows = [
            row(description="hit_into_play", launch_speed=113.4, batter=20),
            row(description="foul", launch_speed=119.9, batter=21),
        ]
        assert self._derive(rows)["hardest_hit"].person_id == 20

    def test_pitch_records_use_pitcher_attribution(self):
        rows = [
            row(release_speed=101.8, pitcher=30, inning_topbot="Top"),
            row(release_speed=69.6, pitcher=31, pitch_name="Slow Curve"),
        ]
        records = self._derive(rows)
        fast, slow = records["fastest_pitch"], records["slowest_pitch"]
        assert (fast.person_id, fast.team_abbr) == (30, "PHI")
        assert slow.value == 69.6
        assert slow.pitch_name == "Slow Curve"

    def test_tie_keeps_first_row(self):
        records = self._derive([hr(440, batter=10), hr(440, batter=11)])
        assert records["longest_hr"].person_id == 10

    def test_missing_values_skipped(self):
        records = self._derive([row(events="home_run", hit_distance_sc="")])
        assert "longest_hr" not in records

    def test_unrequested_stats_not_derived(self):
        records = self._derive([hr(440)], stats=["fastest_pitch"])
        assert set(records) == {"fastest_pitch"}

    def test_empty_rows_empty_records(self):
        assert self._derive([]) == {}


def make_widget(**kwargs):
    from led_ticker_baseball.statcast import MLBStatcastMonitor

    widget = MLBStatcastMonitor(session=kwargs.pop("session", mock.Mock()), **kwargs)
    widget._tz = NY
    return widget


TODAY = dt.date(2026, 6, 12)


class TestSkeleton:
    def test_default_stats_all_four_in_order(self):
        from led_ticker_baseball.statcast import _STAT_KEYS

        assert make_widget().stats == list(_STAT_KEYS)

    def test_default_title(self):
        widget = make_widget()
        widget._set_title()
        assert widget.feed_title.text == "Statcast"

    def test_title_override(self):
        widget = make_widget(title="Robot Numbers")
        widget._set_title()
        assert widget.feed_title.text == "Robot Numbers"

    def test_font_color_override_selected_for_body(self):
        from led_ticker.plugin import make_color

        c = make_color(255, 0, 0)
        widget = make_widget(font_color=c)
        assert widget._body_color() is c
        assert widget._plain_body_color() is c

    def test_default_body_color_is_white(self):
        from led_ticker.colors import RGB_WHITE

        widget = make_widget()
        assert widget._body_color() is RGB_WHITE
        assert widget._plain_body_color() is RGB_WHITE


class TestShouldSkip:
    def test_no_prior_derive_never_skips(self):
        assert make_widget()._should_skip(TODAY, (0, 5)) is False

    def test_gate_fetch_failure_fails_open(self):
        widget = make_widget()
        widget._last_derive = (TODAY, 5)
        assert widget._should_skip(TODAY, None) is False

    def test_skips_when_nothing_changed(self):
        widget = make_widget()
        widget._last_derive = (TODAY, 5)
        assert widget._should_skip(TODAY, (0, 5)) is True

    def test_live_game_forces_derive(self):
        widget = make_widget()
        widget._last_derive = (TODAY, 5)
        assert widget._should_skip(TODAY, (1, 5)) is False

    def test_new_final_forces_derive(self):
        widget = make_widget()
        widget._last_derive = (TODAY, 5)
        assert widget._should_skip(TODAY, (0, 6)) is False

    def test_date_rollover_forces_derive(self):
        widget = make_widget()
        widget._last_derive = (TODAY - dt.timedelta(days=1), 15)
        assert widget._should_skip(TODAY, (0, 15)) is False
