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
