"""Tests for the MLB attendance widget (league superlatives + team mode)."""

import datetime as dt
import unittest.mock as mock
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
TODAY = dt.date(2026, 6, 13)


class TestParseAttendance:
    def test_parses_att_with_commas_and_period(self):
        from led_ticker_baseball.attendance import _parse_attendance

        box = {"info": [{"label": "Att", "value": "19,587."}]}
        assert _parse_attendance(box) == 19587

    def test_missing_att_returns_none(self):
        from led_ticker_baseball.attendance import _parse_attendance

        box = {"info": [{"label": "Weather", "value": "Cloudy."}]}
        assert _parse_attendance(box) is None

    def test_empty_box_returns_none(self):
        from led_ticker_baseball.attendance import _parse_attendance

        assert _parse_attendance({}) is None

    def test_unparseable_value_returns_none(self):
        from led_ticker_baseball.attendance import _parse_attendance

        box = {"info": [{"label": "Att", "value": "n/a"}]}
        assert _parse_attendance(box) is None


class TestFillPct:
    def test_rounds_to_int_percent(self):
        from led_ticker_baseball.attendance import _fill_pct

        assert _fill_pct(19587, 38753) == 51

    def test_capacity_zero_or_missing_returns_none(self):
        from led_ticker_baseball.attendance import _fill_pct

        assert _fill_pct(19587, 0) is None
        assert _fill_pct(19587, None) is None


class TestFormatWeather:
    def test_formats_temp_condition_wind(self):
        from led_ticker_baseball.attendance import _format_weather

        w = {"condition": "Clear", "temp": "72", "wind": "5 mph, In From CF"}
        assert _format_weather(w) == "72° Clear, wind 5 mph, In From CF"

    def test_empty_weather_returns_none(self):
        from led_ticker_baseball.attendance import _format_weather

        assert _format_weather({}) is None
        assert _format_weather(None) is None

    def test_partial_weather_omits_missing_pieces(self):
        from led_ticker_baseball.attendance import _format_weather

        # No wind → just temp + condition; no temp → condition only.
        assert _format_weather({"condition": "Clear", "temp": "72"}) == "72° Clear"
        assert _format_weather({"condition": "Clear"}) == "Clear"


def sched_game(
    pk, state, home="PIT", away="MIA", venue="PNC Park", capacity=38753, game_number=1
):
    """A schedule game shaped like hydrate=venue(fieldInfo),team."""
    return {
        "gamePk": pk,
        "gameNumber": game_number,
        "status": {"abstractGameState": state},
        "teams": {
            "home": {"team": {"abbreviation": home}},
            "away": {"team": {"abbreviation": away}},
        },
        "venue": {"name": venue, "fieldInfo": {"capacity": capacity}},
    }


def schedule(*games):
    return {"dates": [{"games": list(games)}]}


class TestParseScheduleGames:
    def _parse(self, data):
        from led_ticker_baseball.attendance import _parse_schedule_games

        return _parse_schedule_games(data)

    def test_parses_fields(self):
        games = self._parse(schedule(sched_game(1, "Final")))
        g = games[0]
        assert (g.game_pk, g.state, g.home_abbr, g.away_abbr) == (
            1,
            "Final",
            "PIT",
            "MIA",
        )
        assert (g.venue, g.capacity, g.game_number) == ("PNC Park", 38753, 1)

    def test_missing_capacity_is_zero(self):
        data = schedule(
            {
                "gamePk": 2,
                "gameNumber": 1,
                "status": {"abstractGameState": "Final"},
                "teams": {
                    "home": {"team": {"abbreviation": "ATH"}},
                    "away": {"team": {"abbreviation": "LAA"}},
                },
                "venue": {"name": "Sutter Health Park", "fieldInfo": {}},
            }
        )
        assert self._parse(data)[0].capacity == 0

    def test_empty_schedule(self):
        assert self._parse({"dates": []}) == []


def sched_gv(pk, venue, home, capacity, away="OPP", state="Final"):
    from led_ticker_baseball.attendance import GameVenue

    return GameVenue(
        game_pk=pk,
        state=state,
        game_number=1,
        home_abbr=home,
        away_abbr=away,
        venue=venue,
        capacity=capacity,
    )


class TestDeriveSuperlatives:
    def _derive(self, pairs, stats=None):
        from led_ticker_baseball.attendance import _STAT_KEYS, _derive_superlatives

        return _derive_superlatives(pairs, list(stats or _STAT_KEYS))

    def _pairs(self):
        # (GameVenue, attendance) for three final games of varying fill.
        return [
            (sched_gv(1, "Dodger Stadium", "LAD", 56000), 45123),  # 81%
            (sched_gv(2, "Wrigley Field", "CHC", 41649), 41600),  # ~100%
            (sched_gv(3, "PNC Park", "PIT", 38753), 8201),  # 21%
        ]

    def test_biggest_and_smallest_crowd(self):
        recs = self._derive(self._pairs())
        assert recs["biggest_crowd"].value == 45123
        assert recs["biggest_crowd"].venue == "Dodger Stadium"
        assert recs["smallest_crowd"].value == 8201
        assert recs["smallest_crowd"].venue == "PNC Park"

    def test_fullest_and_emptiest_pct(self):
        recs = self._derive(self._pairs())
        assert recs["fullest"].value == 100  # 41600/41649
        assert recs["fullest"].venue == "Wrigley Field"
        assert recs["emptiest"].value == 21  # 8201/38753
        assert recs["emptiest"].venue == "PNC Park"

    def test_pct_skips_zero_capacity_but_crowd_counts_it(self):
        pairs = [
            (sched_gv(1, "PNC Park", "PIT", 38753), 20000),
            (sched_gv(2, "Sutter Health", "ATH", 0), 9000),  # no capacity
        ]
        recs = self._derive(pairs)
        # Smallest crowd still considers the no-capacity game...
        assert recs["smallest_crowd"].value == 9000
        # ...but emptiest/fullest only consider games with capacity.
        assert recs["emptiest"].venue == "PNC Park"
        assert recs["fullest"].venue == "PNC Park"

    def test_record_carries_home_abbr(self):
        recs = self._derive(self._pairs())
        assert recs["biggest_crowd"].home_abbr == "LAD"

    def test_tie_keeps_first(self):
        pairs = [
            (sched_gv(1, "A Park", "PIT", 40000), 30000),
            (sched_gv(2, "B Park", "CHC", 40000), 30000),
        ]
        assert self._derive(pairs)["biggest_crowd"].venue == "A Park"

    def test_unrequested_stats_not_derived(self):
        recs = self._derive(self._pairs(), stats=["biggest_crowd"])
        assert set(recs) == {"biggest_crowd"}

    def test_empty_pairs(self):
        assert self._derive([]) == {}


def make_widget(**kwargs):
    from led_ticker_baseball.attendance import MLBAttendanceMonitor

    widget = MLBAttendanceMonitor(session=kwargs.pop("session", mock.Mock()), **kwargs)
    widget._tz = NY
    return widget


class TestSkeleton:
    def test_default_is_league_mode_all_stats(self):
        from led_ticker_baseball.attendance import _STAT_KEYS

        w = make_widget()
        assert w.team == ""
        assert w.stats == list(_STAT_KEYS)

    def test_team_mode_when_team_set(self):
        assert make_widget(team="tor").team == "tor"  # not upper-cased until start()

    def test_default_title(self):
        w = make_widget()
        w._set_title()
        assert w.feed_title.text == "Attendance"

    def test_title_override(self):
        w = make_widget(title="Turnstiles")
        w._set_title()
        assert w.feed_title.text == "Turnstiles"

    def test_font_color_selected_for_body(self):
        from led_ticker.plugin import make_color

        c = make_color(255, 0, 0)
        w = make_widget(font_color=c)
        assert w._body_color() is c
        assert w._plain_body_color() is c

    def test_default_body_color_is_white(self):
        from led_ticker.colors import RGB_WHITE

        w = make_widget()
        assert w._body_color() is RGB_WHITE
        assert w._plain_body_color() is RGB_WHITE


class TestShouldSkip:
    def test_no_prior_derive_never_skips(self):
        assert make_widget()._should_skip(TODAY, (0, 5)) is False

    def test_gate_failure_fails_open(self):
        w = make_widget()
        w._last_derive = (TODAY, 5)
        assert w._should_skip(TODAY, None) is False

    def test_skips_when_unchanged(self):
        w = make_widget()
        w._last_derive = (TODAY, 5)
        assert w._should_skip(TODAY, (0, 5)) is True

    def test_live_forces_derive(self):
        w = make_widget()
        w._last_derive = (TODAY, 5)
        assert w._should_skip(TODAY, (1, 5)) is False

    def test_new_final_forces_derive(self):
        w = make_widget()
        w._last_derive = (TODAY, 5)
        assert w._should_skip(TODAY, (0, 6)) is False

    def test_date_rollover_forces_derive(self):
        w = make_widget()
        w._last_derive = (TODAY - dt.timedelta(days=1), 5)
        assert w._should_skip(TODAY, (0, 5)) is False
