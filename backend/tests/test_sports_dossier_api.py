"""Sports dossiers enter the same ontology/project path as uploaded documents."""

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app import create_app
from app.api import sports as sports_api
from app.models.project import ProjectManager, ProjectStatus
from app.sports.forecast_context import load_forecast_context
from app.sports.schema import FactCategory, MatchData, MatchFixture, Provenance, SportsFact
from app.sports.snapshots import SnapshotUnavailable
from app.utils.run_budget import BudgetExceeded


KICKOFF = datetime(2030, 1, 2, 18, tzinfo=timezone.utc)
CUTOFF = datetime(2030, 1, 2, 6, tzinfo=timezone.utc)


def _match(*, sport="football", kickoff=KICKOFF):
    fixture = MatchFixture(
        match_id="123",
        sport=sport,
        competition="Premier League" if sport == "football" else "NBA",
        season="2029",
        kickoff_at=kickoff,
        home_team="Home FC" if sport == "football" else "Home Hoops",
        away_team="Away FC" if sport == "football" else "Away Hoops",
        venue=None,
        provenance=Provenance("https://example.org/fixture", datetime(2030, 1, 1, tzinfo=timezone.utc)),
        kickoff_unverified=True,
    )
    facts = (
        SportsFact(
            FactCategory.COACH,
            fixture.home_team,
            "Coach known before cutoff",
            Provenance("https://example.org/coach", datetime(2030, 1, 1, tzinfo=timezone.utc)),
        ),
        SportsFact(
            FactCategory.INJURY_LINEUP,
            fixture.away_team,
            "Lineup posted after cutoff",
            Provenance("https://example.org/lineup", datetime(2030, 1, 2, 12, tzinfo=timezone.utc)),
        ),
    )
    return MatchData(fixture, facts)


@pytest.fixture
def route_setup(tmp_path, monkeypatch):
    monkeypatch.setattr(ProjectManager, "PROJECTS_DIR", str(tmp_path / "projects"))
    monkeypatch.setenv("MIROFISH_SPORTS_SNAPSHOT_DIR", str(tmp_path / "snapshots"))
    monkeypatch.setenv("MIROFISH_SPORTS_HISTORY_DIR", str(tmp_path / "history"))
    monkeypatch.setattr(sports_api.Config, "LLM_PROVIDER", "local")
    seen = {"adapter": [], "llm": [], "scopes": []}

    class FootballAdapter:
        def __init__(self, league, snapshot_store, *, include_lineups):
            seen["adapter"].append(("football", league, snapshot_store.root, include_lineups))

        def load_match(self, match_id, *, cutoff_at):
            seen["adapter"].append((match_id, cutoff_at))
            return _match()

    class BasketballAdapter:
        def __init__(self, snapshot_store):
            seen["adapter"].append(("basketball", snapshot_store.root))

        def load_match(self, match_id, *, cutoff_at):
            seen["adapter"].append((match_id, cutoff_at))
            return _match(sport="basketball")

    class Generator:
        def generate(self, **kwargs):
            seen["llm"].append(kwargs)
            return {
                "entity_types": [{"name": "Team", "description": "Sports team"}],
                "edge_types": [],
                "analysis_summary": "As-of fixture",
            }

    @contextmanager
    def local_scope(project_id):
        seen["scopes"].append(project_id)
        yield

    monkeypatch.setattr(sports_api, "ApiSportsFootballAdapter", FootballAdapter)
    monkeypatch.setattr(sports_api, "ApiSportsNBAAdapter", BasketballAdapter)
    monkeypatch.setattr(sports_api, "OntologyGenerator", Generator)
    monkeypatch.setattr(sports_api, "local_model_run", local_scope)
    return create_app().test_client(), seen


@pytest.mark.parametrize(
    "sport,league",
    [("football", "epl"), ("football", "npfl"), ("basketball", None)],
)
def test_dossier_route_uses_as_of_adapter_and_existing_project_flow(
    route_setup, sport, league
):
    client, seen = route_setup
    payload = {
        "sport": sport,
        "match_id": "123",
        "kickoff_at": "2030-01-02T18:00:00Z",
        "cutoff_hours": 12,
    }
    if league is not None:
        payload["league"] = league

    response = client.post("/api/sports/dossiers", json=payload)

    assert response.status_code == 201
    data = response.get_json()["data"]
    assert data["cutoff_at"] == CUTOFF.isoformat()
    assert data["kickoff_unverified"] is True
    assert data["excluded_fact_count"] == 1
    assert data["baseline_training_matches"] == 0
    assert abs(sum(data["baseline_probabilities"].values()) - 1.0) < 1e-9
    assert "Coach known before cutoff" in data["dossier"]
    assert "Lineup posted after cutoff" not in data["dossier"]
    assert seen["adapter"][-1] == ("123", CUTOFF)
    if sport == "football":
        assert seen["adapter"][0][-1] is True
    assert seen["llm"][0]["domain"] == "sports"
    assert seen["llm"][0]["document_texts"] == [data["dossier"]]
    assert seen["scopes"] == [data["project_id"]]
    project = ProjectManager.get_project(data["project_id"])
    assert project.status == ProjectStatus.ONTOLOGY_GENERATED
    assert project.graph_id is None
    assert project.files == [{"filename": "match-dossier.md", "size": len(data["dossier"].encode())}]
    assert ProjectManager.get_extracted_text(project.project_id) == data["dossier"]
    assert load_forecast_context(project.project_id)["dossier_sha256"]
    source_files = ProjectManager.get_project_files(project.project_id)
    assert len(source_files) == 1
    assert Path(source_files[0]).read_text(encoding="utf-8") == data["dossier"]


@pytest.mark.parametrize(
    "payload",
    [
        {"sport": "football", "league": "epl", "match_id": "123", "kickoff_at": "2030-01-02T18:00:00Z"},
        {"sport": "football", "league": "epl", "match_id": "123", "cutoff_hours": 12},
        {"sport": "football", "match_id": "123", "kickoff_at": "2030-01-02T18:00:00Z", "cutoff_hours": 12},
        {"sport": "football", "league": "epl", "match_id": "123", "kickoff_at": "2030-01-02T18:00:00", "cutoff_hours": 12},
        {"sport": "basketball", "league": "npfl", "match_id": "123", "kickoff_at": "2030-01-02T18:00:00Z", "cutoff_hours": 12},
        {"sport": "football", "league": "epl", "match_id": -1, "kickoff_at": "2030-01-02T18:00:00Z", "cutoff_hours": 12},
    ],
)
def test_invalid_dossier_request_creates_no_project(route_setup, payload):
    client, seen = route_setup
    response = client.post("/api/sports/dossiers", json=payload)
    assert response.status_code == 400
    assert seen["adapter"] == []
    assert ProjectManager.list_projects(limit=None) == []


def test_kickoff_mismatch_rejected_before_project_creation(route_setup, monkeypatch):
    client, seen = route_setup

    class WrongKickoffAdapter:
        def __init__(self, league, snapshot_store, *, include_lineups):
            pass

        def load_match(self, match_id, *, cutoff_at):
            assert cutoff_at == CUTOFF
            return _match(kickoff=datetime(2030, 1, 2, 20, tzinfo=timezone.utc))

    monkeypatch.setattr(sports_api, "ApiSportsFootballAdapter", WrongKickoffAdapter)
    response = client.post(
        "/api/sports/dossiers",
        json={"sport": "football", "league": "epl", "match_id": "123", "kickoff_at": "2030-01-02T18:00:00Z", "cutoff_hours": 12},
    )
    assert response.status_code == 409
    assert seen["llm"] == []
    assert ProjectManager.list_projects(limit=None) == []


def test_snapshot_unavailable_creates_no_project(route_setup, monkeypatch):
    client, _ = route_setup

    class NoSnapshotAdapter:
        def __init__(self, league, snapshot_store, *, include_lineups):
            pass

        def load_match(self, match_id, *, cutoff_at):
            raise SnapshotUnavailable("No pre-cutoff capture")

    monkeypatch.setattr(sports_api, "ApiSportsFootballAdapter", NoSnapshotAdapter)
    response = client.post(
        "/api/sports/dossiers",
        json={"sport": "football", "league": "epl", "match_id": "123", "kickoff_at": "2030-01-02T18:00:00Z", "cutoff_hours": 12},
    )
    assert response.status_code == 409
    assert ProjectManager.list_projects(limit=None) == []


@pytest.mark.parametrize("failure,status", [(RuntimeError("failed"), 500), (BudgetExceeded("limit"), 429)])
def test_ontology_failure_removes_incomplete_project(route_setup, monkeypatch, failure, status):
    client, _ = route_setup

    class FailingGenerator:
        def generate(self, **kwargs):
            raise failure

    monkeypatch.setattr(sports_api, "OntologyGenerator", FailingGenerator)
    response = client.post(
        "/api/sports/dossiers",
        json={"sport": "football", "league": "epl", "match_id": "123", "kickoff_at": "2030-01-02T18:00:00Z", "cutoff_hours": 12},
    )
    assert response.status_code == status
    assert ProjectManager.list_projects(limit=None) == []
