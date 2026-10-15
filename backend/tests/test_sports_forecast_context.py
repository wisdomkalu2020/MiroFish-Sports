"""The live baseline uses only imported results captured before cutoff."""

from datetime import date, datetime, timezone

import pytest

from app.models.project import ProjectManager
from app.sports.forecast_context import (
    ForecastContextError,
    build_forecast_context,
    load_forecast_context,
    save_forecast_context,
)
from app.sports.history import HistoricalMatch
from app.sports.schema import MatchFixture, Provenance


CUTOFF = datetime(2030, 1, 2, 6, tzinfo=timezone.utc)
KICKOFF = datetime(2030, 1, 3, 6, tzinfo=timezone.utc)


def _fixture(sport="football"):
    return MatchFixture(
        match_id="future", sport=sport, competition="EPL" if sport == "football" else "NBA",
        season="2029-30", kickoff_at=KICKOFF, home_team="Home", away_team="Away",
        venue=None, provenance=Provenance("https://example.org/fixture", CUTOFF),
    )


def _result(match_id, *, fetched_at, sport="football"):
    return HistoricalMatch(
        match_id=match_id, sport=sport, league="EPL" if sport == "football" else "NBA",
        season="2029-30", home_team="Home", away_team="Away",
        match_date=date(2029, 12, 20), kickoff_at=None, kickoff_unverified=True,
        home_score=2 if sport == "football" else 102,
        away_score=1 if sport == "football" else 98,
        result_available_at=datetime(2029, 12, 22, tzinfo=timezone.utc),
        source="https://example.org/results", fetched_at=fetched_at,
    )


def test_forecast_excludes_late_import_even_when_result_is_old():
    early = _result("early", fetched_at=datetime(2029, 12, 23, tzinfo=timezone.utc))
    late = _result("late", fetched_at=datetime(2030, 1, 4, tzinfo=timezone.utc))
    context = build_forecast_context(
        project_id="proj_123", fixture=_fixture(), league="epl", cutoff_at=CUTOFF,
        cutoff_hours=24, dossier_markdown="# pre-match", history=(early, late),
    )
    assert context["training_matches"] == 1
    assert context["training_match_ids"] == ["early"]
    assert context["baseline_method"] == "equal_weight_football_elo_dixon_coles"
    assert abs(sum(context["baseline_probabilities"].values()) - 1.0) < 1e-9


def test_context_is_immutable_and_bound_to_dossier(tmp_path, monkeypatch):
    monkeypatch.setattr(ProjectManager, "PROJECTS_DIR", str(tmp_path / "projects"))
    project = ProjectManager.create_project("sports")
    ProjectManager.save_extracted_text(project.project_id, "# pre-match")
    context = build_forecast_context(
        project_id=project.project_id, fixture=_fixture("basketball"), league="nba",
        cutoff_at=CUTOFF, cutoff_hours=24, dossier_markdown="# pre-match", history=(),
    )
    save_forecast_context(project.project_id, context)
    assert load_forecast_context(project.project_id)["training_matches"] == 0
    with pytest.raises(ForecastContextError, match="already exists"):
        save_forecast_context(project.project_id, context)
    ProjectManager.save_extracted_text(project.project_id, "# changed")
    with pytest.raises(ForecastContextError, match="changed"):
        load_forecast_context(project.project_id)
