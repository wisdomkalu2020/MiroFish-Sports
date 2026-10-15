"""Walk-forward tests focus on temporal leakage and scored probability quality."""

import json
from datetime import date, datetime, timezone
from math import log

import pytest

from app.sports.evaluation import AgentPrediction, MarketOdds, devig_decimal_odds, evaluate_walk_forward
from app.sports.history import HistoricalMatch, conservative_result_available_at
from scripts.evaluate_sports_baselines import main


FETCHED = datetime(2026, 12, 1, tzinfo=timezone.utc)


def _match(match_id: str, day: int, home: str, away: str, home_score: int, away_score: int,
           *, sport: str = "football", league: str = "EPL") -> HistoricalMatch:
    match_date = date(2026, 10, day)
    return HistoricalMatch(
        match_id=match_id,
        sport=sport,
        league=league,
        season="2026-27" if sport == "football" else "2027",
        home_team=home,
        away_team=away,
        match_date=match_date,
        kickoff_at=None,
        kickoff_unverified=True,
        home_score=home_score,
        away_score=away_score,
        result_available_at=conservative_result_available_at(match_date),
        source="https://example.test/source",
        fetched_at=FETCHED,
    )


def test_daily_batch_and_result_availability_prevent_score_leakage():
    first = _match("first", 1, "A", "B", 8, 0)
    same_day = _match("same-day", 1, "A", "C", 1, 0)
    next_day = _match("next-day", 2, "A", "B", 0, 1)
    third_day = _match("third-day", 3, "A", "B", 0, 2)
    report = evaluate_walk_forward([third_day, same_day, next_day, first], cutoff_hours=0)
    rows = {row["match_id"]: row for row in report["predictions"]}
    assert rows["first"]["training_matches"] == 0
    assert rows["same-day"]["training_matches"] == 0
    assert rows["next-day"]["training_matches"] == 0
    assert rows["third-day"]["training_matches"] == 2
    assert rows["first"]["probabilities"]["naive"] == rows["same-day"]["probabilities"]["naive"]
    assert rows["first"]["probabilities"]["elo"] == rows["same-day"]["probabilities"]["elo"]

    changed = _match("first", 1, "A", "B", 0, 8)
    changed_rows = {row["match_id"]: row for row in evaluate_walk_forward(
        [changed, same_day, next_day, third_day], cutoff_hours=0
    )["predictions"]}
    assert changed_rows["same-day"]["probabilities"] == rows["same-day"]["probabilities"]
    assert changed_rows["third-day"]["probabilities"]["elo"] != rows["third-day"]["probabilities"]["elo"]


def test_explicit_cutoff_can_hold_back_old_result():
    history = [_match("old", 1, "A", "B", 2, 1), _match("target", 3, "A", "B", 0, 1)]
    immediate = evaluate_walk_forward(history, cutoff_hours=0)
    one_day = evaluate_walk_forward(history, cutoff_hours=24)
    assert immediate["predictions"][1]["training_matches"] == 1
    assert one_day["predictions"][1]["training_matches"] == 0
    assert one_day["predictions"][1]["cutoff_at"] == "2026-10-02T00:00:00+00:00"


def test_holdout_start_warms_only_with_results_available_by_each_cutoff():
    history = [
        _match("warm-ready", 1, "A", "B", 1, 0),
        _match("warm-later", 2, "C", "D", 2, 0),
        _match("held-first", 3, "A", "C", 0, 1),
        _match("held-later", 5, "B", "D", 1, 1),
    ]
    report = evaluate_walk_forward(
        history, cutoff_hours=0, evaluation_start=date(2026, 10, 3),
    )
    assert report["match_count"] == 2
    assert report["history_match_count"] == 4
    assert [row["match_id"] for row in report["predictions"]] == ["held-first", "held-later"]
    assert report["predictions"][0]["training_matches"] == 1
    assert report["predictions"][1]["training_matches"] == 3
    assert report["variants"]["naive"]["count"] == 2


def test_basketball_scores_naive_and_elo_without_external_variants():
    report = evaluate_walk_forward(
        [_match("nba1", 1, "A", "B", 101, 99, sport="basketball", league="NBA")],
        cutoff_hours=24,
    )
    assert report["variants"]["naive"]["log_loss"] == pytest.approx(log(2))
    assert report["variants"]["naive"]["brier_score"] == pytest.approx(0.5)
    assert report["variants"]["elo"]["status"] == "scored"
    assert "dixon_coles" not in report["variants"]
    assert report["variants"]["baseline_plus_agents"]["status"] == "unavailable"
    assert report["variants"]["bookmaker"]["status"] == "unavailable"
    assert sum(bucket["count"] for bucket in report["variants"]["naive"]["calibration"]["home"]) == 1


def test_external_rows_require_pre_cutoff_capture_and_agent_leakage_control():
    match = _match("final", 10, "A", "B", 1, 0)
    before = datetime(2026, 10, 8, tzinfo=timezone.utc)
    after = datetime(2026, 10, 10, tzinfo=timezone.utc)
    agent_old = AgentPrediction("final", before, {"home": .7, "draw": .2, "away": .1}, "anonymized")
    agent_late = AgentPrediction("final", after, {"home": .99, "draw": .005, "away": .005}, "anonymized")
    quote_old = MarketOdds("final", before, {"home": 2.0, "draw": 4.0, "away": 4.0}, "example bookmaker")
    quote_late = MarketOdds("final", after, {"home": 1.01, "draw": 100, "away": 100}, "after cutoff")
    report = evaluate_walk_forward(
        [match], cutoff_hours=24,
        agent_predictions=[agent_old, agent_late], market_odds=[quote_old, quote_late],
    )
    row = report["predictions"][0]
    assert row["probabilities"]["baseline_plus_agents"] == agent_old.probabilities
    assert row["probabilities"]["bookmaker"] == pytest.approx({"home": .5, "draw": .25, "away": .25})
    assert row["bookmaker_source"] == "example bookmaker"
    assert report["variants"]["baseline_plus_agents"]["count"] == 1
    assert report["variants"]["bookmaker"]["count"] == 1
    assert report["paired_comparisons"]["agents_vs_baseline"]["count"] == 1
    assert report["paired_comparisons"]["agents_vs_baseline"]["baseline_variant"] == "combined"

    future_training = AgentPrediction(
        "final", before, {"home": .7, "draw": .2, "away": .1},
        "post_training_cutoff", datetime(2026, 10, 12, tzinfo=timezone.utc),
    )
    blocked = evaluate_walk_forward([match], cutoff_hours=24, agent_predictions=[future_training])
    assert blocked["variants"]["baseline_plus_agents"]["status"] == "unavailable"
    assert blocked["paired_comparisons"]["agents_vs_baseline"]["status"] == "unavailable"


def test_invalid_external_probabilities_and_odds_are_rejected():
    match = _match("final", 10, "A", "B", 1, 0)
    before = datetime(2026, 10, 8, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="sum to one"):
        evaluate_walk_forward([match], cutoff_hours=24, agent_predictions=[
            AgentPrediction("final", before, {"home": .7, "draw": .2, "away": .2}, "anonymized")
        ])
    with pytest.raises(ValueError, match="greater than one"):
        devig_decimal_odds({"home": 1.0, "draw": 4.0, "away": 4.0}, ("home", "draw", "away"))
    with pytest.raises(ValueError, match="cutoff_hours"):
        evaluate_walk_forward([match], cutoff_hours=-1)


def test_cli_reads_local_source_and_writes_auditable_report(tmp_path, capsys):
    source = tmp_path / "epl.txt"
    source.write_text(
        "Mon Oct 5 2026\nA v B 2-1\nThu Oct 8 2026\nC v D 0-0\n",
        encoding="utf-8",
    )
    output = tmp_path / "report.json"
    assert main([
        "--football-file", "EPL", "2026-27", str(source),
        "https://example.test/epl.txt", "2026-12-01T00:00:00Z",
        "--cutoff-hours", "24", "--evaluate-from", "2026-10-08", "--output", str(output),
    ]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["match_count"] == 1
    assert report["history_match_count"] == 2
    assert report["predictions"][0]["training_matches"] == 1
    assert "predictions" not in json.loads(capsys.readouterr().out)
