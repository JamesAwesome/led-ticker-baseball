"""Tests for the MLB attendance widget (league superlatives + team mode)."""


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
