"""Tests for the MLB promotions widget and the shared resolve_team_id helper."""

import datetime as dt
import unittest.mock as mock


def _ctx(json_value):
    """Async context manager mock whose response .json() returns json_value."""
    resp = mock.AsyncMock()
    resp.json.return_value = json_value
    ctx = mock.AsyncMock()
    ctx.__aenter__.return_value = resp
    return ctx


def make_session(routes):
    """Mock aiohttp session routing by URL substring; first match wins."""
    session = mock.MagicMock()

    def side_effect(url, *args, **kwargs):
        for key, payload in routes.items():
            if key in url:
                return _ctx(payload)
        return _ctx({})

    session.get.side_effect = side_effect
    return session


TEAMS_PAYLOAD = {
    "teams": [
        {"id": 141, "abbreviation": "TOR"},
        {"id": 147, "abbreviation": "NYY"},
    ]
}


class TestResolveTeamId:
    async def test_resolves_known_abbreviation(self):
        from led_ticker_baseball.teams import resolve_team_id

        session = make_session({"/teams": TEAMS_PAYLOAD})
        assert await resolve_team_id(session, "TOR") == 141

    async def test_unknown_abbreviation_returns_none(self):
        from led_ticker_baseball.teams import resolve_team_id

        session = make_session({"/teams": TEAMS_PAYLOAD})
        assert await resolve_team_id(session, "ZZZ") is None

    async def test_request_failure_returns_none(self):
        from led_ticker_baseball.teams import resolve_team_id

        session = mock.MagicMock()
        session.get.side_effect = RuntimeError("network down")
        assert await resolve_team_id(session, "TOR") is None


class TestCleanPromoName:
    def test_strips_presented_by(self):
        from led_ticker_baseball.promotions import _clean_promo_name

        assert (
            _clean_promo_name("Loonie Dogs Night presented by Schneiders")
            == "Loonie Dogs Night"
        )

    def test_strips_pres_by(self):
        from led_ticker_baseball.promotions import _clean_promo_name

        assert _clean_promo_name("Loonie Dogs Night pres. by Schneiders") == (
            "Loonie Dogs Night"
        )

    def test_case_insensitive(self):
        from led_ticker_baseball.promotions import _clean_promo_name

        assert _clean_promo_name("Pride Night Presented By TD") == "Pride Night"

    def test_no_sponsor_unchanged(self):
        from led_ticker_baseball.promotions import _clean_promo_name

        assert _clean_promo_name("Canada Day") == "Canada Day"


class TestDedupePromos:
    def test_exact_duplicates_collapse(self):
        from led_ticker_baseball.promotions import _dedupe_promos

        assert _dedupe_promos(["Pride Night", "pride night"]) == ["Pride Night"]

    def test_prefix_duplicate_keeps_shorter_seen_first(self):
        from led_ticker_baseball.promotions import _dedupe_promos

        names = [
            "Dylan Cease Bobblehead Giveaway",
            "Dylan Cease Bobblehead Giveaway Night",
        ]
        assert _dedupe_promos(names) == ["Dylan Cease Bobblehead Giveaway"]

    def test_prefix_duplicate_keeps_shorter_seen_second(self):
        from led_ticker_baseball.promotions import _dedupe_promos

        names = [
            "Dylan Cease Bobblehead Giveaway Night",
            "Dylan Cease Bobblehead Giveaway",
        ]
        assert _dedupe_promos(names) == ["Dylan Cease Bobblehead Giveaway"]

    def test_distinct_names_kept_in_order(self):
        from led_ticker_baseball.promotions import _dedupe_promos

        names = ["Loonie Dogs Night", "Pride Night"]
        assert _dedupe_promos(names) == names


class TestMatchAny:
    def test_case_insensitive_substring(self):
        from led_ticker_baseball.promotions import _match_any

        assert _match_any("Loonie Dogs Night", ["loonie dogs"])

    def test_no_match(self):
        from led_ticker_baseball.promotions import _match_any

        assert not _match_any("Pride Night", ["bobblehead"])

    def test_empty_keywords_never_match(self):
        from led_ticker_baseball.promotions import _match_any

        assert not _match_any("Pride Night", [])


class TestGameLocalDate:
    def test_official_date_preferred(self):
        from zoneinfo import ZoneInfo

        from led_ticker_baseball.promotions import _game_local_date

        g = {"officialDate": "2026-06-23", "gameDate": "2026-06-24T02:15:00Z"}
        tz = ZoneInfo("America/New_York")
        assert _game_local_date(g, tz) == dt.date(2026, 6, 23)

    def test_game_date_fallback_converts_timezone(self):
        from zoneinfo import ZoneInfo

        from led_ticker_baseball.promotions import _game_local_date

        # 02:15 UTC = 22:15 the previous day in New York
        g = {"gameDate": "2026-06-24T02:15:00Z"}
        tz = ZoneInfo("America/New_York")
        assert _game_local_date(g, tz) == dt.date(2026, 6, 23)

    def test_missing_dates_return_none(self):
        from zoneinfo import ZoneInfo

        from led_ticker_baseball.promotions import _game_local_date

        assert _game_local_date({}, ZoneInfo("America/New_York")) is None


def make_game(home_id, official_date, promos=()):
    """Minimal schedule-game payload. home_id 141 = TOR (the tested team)."""
    return {
        "officialDate": official_date,
        "teams": {
            "home": {"team": {"id": home_id}},
            "away": {"team": {"id": 999}},
        },
        "promotions": [{"name": n} for n in promos],
    }


def make_schedule(*games):
    return {"dates": [{"games": list(games)}]}


def make_widget(**kwargs):
    from led_ticker_baseball.promotions import MLBPromotionsMonitor

    widget = MLBPromotionsMonitor(
        session=kwargs.pop("session", mock.Mock()),
        team="TOR",
        **kwargs,
    )
    widget._team_id = 141
    return widget


class TestParseHomeGames:
    def _parse(self, data):
        from zoneinfo import ZoneInfo

        widget = make_widget()
        return widget._parse_home_games(data, ZoneInfo("America/New_York"))

    def test_home_games_only(self):
        data = make_schedule(
            make_game(141, "2026-06-23", promos=["Loonie Dogs Night"]),
            make_game(144, "2026-06-24", promos=["Bobblehead Giveaway"]),  # away
        )
        games, had_games = self._parse(data)
        assert had_games is True
        assert len(games) == 1
        assert games[0].promos == ["Loonie Dogs Night"]

    def test_promos_cleaned_and_deduped(self):
        data = make_schedule(
            make_game(
                141,
                "2026-06-10",
                promos=[
                    "Dylan Cease Bobblehead Giveaway Night",
                    "Dylan Cease Bobblehead Giveaway presented by Rogers",
                ],
            ),
        )
        games, _ = self._parse(data)
        assert games[0].promos == ["Dylan Cease Bobblehead Giveaway"]

    def test_doubleheader_promos_merged_by_date(self):
        data = make_schedule(
            make_game(141, "2026-06-23", promos=["Loonie Dogs Night"]),
            make_game(141, "2026-06-23", promos=["Pride Night"]),
        )
        games, _ = self._parse(data)
        assert len(games) == 1
        assert games[0].game_date == dt.date(2026, 6, 23)
        assert games[0].promos == ["Loonie Dogs Night", "Pride Night"]

    def test_sorted_by_date(self):
        data = make_schedule(
            make_game(141, "2026-06-30", promos=["Loonie Dogs Night"]),
            make_game(141, "2026-06-23", promos=["Pride Night"]),
        )
        games, _ = self._parse(data)
        assert [g.game_date.day for g in games] == [23, 30]

    def test_empty_schedule(self):
        games, had_games = self._parse({"dates": []})
        assert games == []
        assert had_games is False

    def test_away_only_sets_had_games(self):
        data = make_schedule(make_game(144, "2026-06-24"))
        games, had_games = self._parse(data)
        assert games == []
        assert had_games is True
