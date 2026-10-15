"""Meaningful probability and as-of checks for the non-LLM models."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.sports.baseline import BasketballBaseline, FootballBaseline


NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def _result(
    match_id: str,
    sport: str,
    league: str,
    home: str,
    away: str,
    score: tuple[int, int],
    available_at: datetime = NOW,
):
    return SimpleNamespace(
        match_id=match_id,
        sport=sport,
        league=league,
        home_team=home,
        away_team=away,
        home_score=score[0],
        away_score=score[1],
        result_available_at=available_at,
    )


def test_football_probabilities_and_scorelines_sum_to_one():
    model = FootballBaseline("epl")
    for index in range(40):
        score = ((index % 4), ((index * 3) % 3))
        model.observe(
            _result(str(index), "football", "epl", "Home", "Away", score, NOW + timedelta(days=index)),
            as_of=NOW + timedelta(days=index),
        )

    forecast = model.predict("Home", "Away")
    assert forecast.training_matches == 40
    assert set(forecast.elo) == {"home", "draw", "away"}
    assert set(forecast.dixon_coles) == {"home", "draw", "away"}
    assert sum(forecast.elo.values()) == pytest.approx(1.0)
    assert sum(forecast.dixon_coles.values()) == pytest.approx(1.0)
    assert sum(forecast.combined.values()) == pytest.approx(1.0)
    assert sum(forecast.scorelines.values()) == pytest.approx(1.0)
    assert forecast.dixon_coles["draw"] == pytest.approx(
        sum(prob for scoreline, prob in forecast.scorelines.items() if scoreline[0] == scoreline[2])
    )


def test_result_cannot_affect_a_forecast_before_its_availability():
    model = FootballBaseline("npfl")
    before = model.predict("A", "B")
    future = _result("future", "football", "npfl", "A", "B", (8, 0), NOW + timedelta(days=2))
    with pytest.raises(ValueError, match="not available"):
        model.observe(future, as_of=NOW)
    assert model.predict("A", "B") == before
    model.observe(future, as_of=NOW + timedelta(days=2))
    assert model.predict("A", "B").combined["home"] > before.combined["home"]
    with pytest.raises(ValueError, match="duplicate"):
        model.observe(future, as_of=NOW + timedelta(days=2))


def test_basketball_tie_rejected_without_mutating_rating():
    model = BasketballBaseline("nba")
    before = model.predict("A", "B")
    tied = _result("tied", "basketball", "nba", "A", "B", (100, 100))
    with pytest.raises(ValueError, match="cannot be tied"):
        model.observe(tied, as_of=NOW)
    assert model.predict("A", "B") == before
    win = _result("win", "basketball", "nba", "A", "B", (101, 100))
    model.observe(win, as_of=NOW)
    after = model.predict("A", "B")
    assert after.elo["home"] > before.elo["home"]
    assert sum(after.elo.values()) == pytest.approx(1.0)


def test_observation_order_and_league_are_enforced():
    model = FootballBaseline("epl")
    model.observe(
        _result("later", "football", "epl", "A", "B", (2, 1), NOW + timedelta(days=1)),
        as_of=NOW + timedelta(days=1),
    )
    with pytest.raises(ValueError, match="availability order"):
        model.observe(_result("earlier", "football", "epl", "C", "D", (1, 0)), as_of=NOW + timedelta(days=1))
    with pytest.raises(ValueError, match="different sport or league"):
        model.observe(_result("wrong", "football", "npfl", "C", "D", (1, 0), NOW + timedelta(days=1)), as_of=NOW + timedelta(days=1))
