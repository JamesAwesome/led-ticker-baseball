"""Tests for the MLB promotions widget and the shared resolve_team_id helper."""

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
        import datetime as dt
        from zoneinfo import ZoneInfo

        from led_ticker_baseball.promotions import _game_local_date

        g = {"officialDate": "2026-06-23", "gameDate": "2026-06-24T02:15:00Z"}
        tz = ZoneInfo("America/New_York")
        assert _game_local_date(g, tz) == dt.date(2026, 6, 23)

    def test_game_date_fallback_converts_timezone(self):
        import datetime as dt
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
