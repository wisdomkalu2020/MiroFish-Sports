"""Sports-only OASIS vote layer; no Zep or live model is required."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.sports.agent_layer import (
    SportsAgentError,
    _bounded_final,
    collect_sports_votes,
    is_sports_dossier,
    label_synthetic_report,
    validate_forecast_context,
    mark_synthetic_profiles,
    read_oasis_actions,
    render_sports_forecast_section,
    select_sports_entities,
    sports_agent_limit,
    sports_round_limit,
)
from app.sports.agent_backtest import (
    AgentBacktestError,
    plan_agent_backtest,
    run_agent_backtest_sample,
)
from app.models.project import ProjectStatus
from app.services.zep_entity_reader import EntityNode, FilteredEntities


DOSSIER = """# Pre-match dossier: Alpha vs Beta

- Match ID: 77
- Sport: football
- Competition: Test league; season: 2026
- Scheduled kickoff (UTC): 2026-10-14T18:00:00+00:00
- Evidence cutoff (UTC): 2026-10-14T12:00:00+00:00 (6 hours before scheduled kickoff)
- Fixture source: schedule (fetched 2026-10-14T11:00:00+00:00)
- Data warning: lineup not confirmed

## Recent form

- Alpha: unbeaten in two prior fixtures [source: archive; captured: 2026-10-14T10:00:00+00:00]
- Beta: one win in three prior fixtures [source: archive; captured: 2026-10-14T10:00:00+00:00]
"""


def _context() -> dict:
    return {
        "project_id": "proj_123",
        "sport": "football",
        "cutoff_at": "2026-10-14T12:00:00+00:00",
        "dossier_sha256": hashlib.sha256(DOSSIER.encode()).hexdigest(),
        "baseline_probabilities": {"home": 0.50, "draw": 0.25, "away": 0.25},
        "baseline_method": "equal_weight_football_elo_dixon_coles",
        "training_matches": 1,
        "training_match_ids": ["historic-1"],
        "training_latest_fetch_at": "2026-10-12T20:00:00+00:00",
        "warnings": [],
    }


class _Votes:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def chat_json(self, **kwargs):
        self.calls.append(kwargs)
        return next(self.responses)


def _vote(home=0.05, draw=-0.02, away=-0.03):
    return {
        "adjustments": {"home": home, "draw": draw, "away": away},
        "fact_ids": ["F001"],
        "rationale": "Alpha's archived recent form supports a small home increase.",
    }


def test_context_rejects_changed_dossier_and_future_source():
    context = _context()
    loaded = validate_forecast_context(
        context, project_id="proj_123", simulation_id="sim_1",
        dossier_text=DOSSIER,
    )
    assert loaded["baseline_probabilities"]["home"] == 0.5
    with pytest.raises(SportsAgentError, match="does not match"):
        validate_forecast_context(
            context, project_id="proj_123", simulation_id="sim_1",
            dossier_text=DOSSIER + "New fact",
        )
    context["training_latest_fetch_at"] = "2026-10-15T00:00:00+00:00"
    with pytest.raises(SportsAgentError, match="fetched after the cutoff"):
        validate_forecast_context(
            context, project_id="proj_123", simulation_id="sim_1",
            dossier_text=DOSSIER,
        )


def test_synthetic_profiles_and_limits_leave_generic_detection_untouched(monkeypatch):
    monkeypatch.setenv("MIROFISH_SPORTS_AGENT_COUNT", "10")
    monkeypatch.setenv("MIROFISH_SPORTS_MAX_ROUNDS", "3")
    assert sports_agent_limit() == 10
    assert sports_round_limit() == 3
    assert is_sports_dossier(DOSSIER)
    assert not is_sports_dossier("# Ordinary MiroFish seed")

    entities = [
        SimpleNamespace(name=f"Player {i}", get_entity_type=lambda: "Player")
        for i in range(18)
    ]
    assert len(select_sports_entities(entities)) == 10
    profiles = [SimpleNamespace(
        name="Real Coach", user_name="real_coach", bio="Actual coach",
        persona="Discuss the match", source_entity_type="Coach",
    )]
    mark_synthetic_profiles(profiles)
    assert profiles[0].name.startswith("Synthetic Coach perspective")
    assert profiles[0].user_name.startswith("synthetic_sports_")
    assert "not an actual person" in profiles[0].bio


def test_agent_votes_use_oasis_rounds_and_remain_bounded():
    profiles = [{"user_id": i, "profession": "Analyst"} for i in range(10)]
    actions = [
        {"agent_id": i, "round": 1, "action_type": "CREATE_POST",
         "action_args": {"content": "Synthetic discussion only"}, "success": True}
        for i in range(10)
    ] + [
        {"agent_id": 0, "round": 2, "action_type": "CREATE_COMMENT",
         "action_args": {"content": "Another synthetic discussion"}, "success": True},
    ]
    llm = _Votes([_vote() for _ in profiles])
    result = collect_sports_votes(
        context=_context(), dossier_text=DOSSIER, profiles=profiles,
        actions=actions, llm=llm, simulation_id="sim_1",
    )
    assert len(result["votes"]) == 10
    assert result["votes"][0]["discussion_rounds_seen"] == [1, 2]
    assert result["votes"][0]["synthetic"] is True
    assert result["consensus"]["fact_ids"] == ["F001"]
    assert result["final_probabilities"]["home"] == pytest.approx(0.55)
    assert sum(result["final_probabilities"].values()) == pytest.approx(1)
    assert "lineup not confirmed" in " ".join(result["data_freshness_warnings"])
    assert "synthetic_oasis_discussion_not_evidence" in llm.calls[0]["messages"][1]["content"]
    section = render_sports_forecast_section(result)
    assert "Synthetic consensus" in section
    assert "not statements by real coaches" in section
    report = label_synthetic_report("# Match report\n\nNarrative")
    assert report.index("Synthetic pre-match simulation") < report.index("Narrative")


def test_invalid_votes_are_withheld_and_baseline_remains_primary():
    profiles = [{"user_id": i} for i in range(10)]
    llm = _Votes([_vote(home=0.40, draw=-0.2, away=-0.2) for _ in profiles])
    actions = [
        {"agent_id": i, "round": 1, "action_type": "CREATE_POST",
         "action_args": {"content": "Synthetic post"}, "success": True}
        for i in range(10)
    ]
    result = collect_sports_votes(
        context=_context(), dossier_text=DOSSIER, profiles=profiles,
        actions=actions, llm=llm, simulation_id="sim_1",
    )
    assert result["rejected_vote_count"] == 10
    assert result["votes"] == []
    assert result["final_probabilities"] == pytest.approx(_context()["baseline_probabilities"])
    assert "withheld" in " ".join(result["data_freshness_warnings"])


def test_agent_votes_require_recorded_oasis_discussion():
    profiles = [{"user_id": i} for i in range(10)]
    llm = _Votes([_vote() for _ in profiles])
    with pytest.raises(SportsAgentError, match="no synthetic discussion text"):
        collect_sports_votes(
            context=_context(), dossier_text=DOSSIER, profiles=profiles,
            actions=[], llm=llm, simulation_id="sim_1",
        )
    assert llm.calls == []


def test_only_oasis_participants_can_vote():
    profiles = [{"user_id": i} for i in range(10)]
    actions = [
        {"agent_id": i, "round": 1, "action_type": "CREATE_POST",
         "action_args": {"content": "Synthetic post"}, "success": True}
        for i in range(8)
    ]
    llm = _Votes([_vote() for _ in range(8)])
    result = collect_sports_votes(
        context=_context(), dossier_text=DOSSIER, profiles=profiles,
        actions=actions, llm=llm, simulation_id="sim_1",
    )
    assert [vote["agent_id"] for vote in result["votes"]] == list(range(8))
    assert len(llm.calls) == 8


def test_box_projection_handles_a_rare_outcome():
    baseline = {"home": 0.92, "draw": 0.04, "away": 0.04}
    projected = _bounded_final(baseline, {"home": 0.10, "draw": -0.05, "away": -0.05})
    assert all(0 <= value <= 1 for value in projected.values())
    assert sum(projected.values()) == pytest.approx(1)
    assert all(abs(projected[key] - baseline[key]) <= 0.100000001 for key in baseline)


def test_oasis_action_reader_excludes_events(tmp_path: Path):
    folder = tmp_path / "twitter"
    folder.mkdir()
    (folder / "actions.jsonl").write_text(
        '{"event_type":"round_end","round":1}\n'
        '{"agent_id":2,"round":1,"action_type":"CREATE_POST","action_args":{"content":"view"}}\n',
        encoding="utf-8",
    )
    actions = read_oasis_actions(tmp_path)
    assert len(actions) == 1
    assert actions[0]["agent_id"] == 2


def test_agent_backtest_cap_preflights_before_any_graph_creation():
    created = []
    projects = {
        "proj_1": SimpleNamespace(status=ProjectStatus.ONTOLOGY_GENERATED, graph_id=None),
        "proj_2": SimpleNamespace(status=ProjectStatus.ONTOLOGY_GENERATED, graph_id=None),
    }

    def context_loader(project_id):
        context = _context()
        context["project_id"] = project_id
        context["agent_backtest_leakage_method"] = "anonymized"
        return context

    def create_graph(project_id):
        created.append(project_id)
        return "zep_" + project_id

    kwargs = {
        "project_loader": projects.get,
        "context_loader": context_loader,
        "dossier_loader": lambda _: DOSSIER,
    }
    with pytest.raises(AgentBacktestError, match="sample must contain"):
        run_agent_backtest_sample(
            ["proj_1", "proj_2"], create_graph=create_graph,
            simulate_and_report=lambda *_: {}, max_graphs=1,
            require_zep=False, **kwargs,
        )
    assert created == []

    results = run_agent_backtest_sample(
        ["proj_1", "proj_2"], create_graph=create_graph,
        simulate_and_report=lambda project_id, graph_id, _: {
            "project_id": project_id, "graph_id": graph_id,
        }, max_graphs=2, require_zep=False, **kwargs,
    )
    assert created == ["proj_1", "proj_2"]
    assert len(results) == 2


def test_agent_backtest_refuses_named_historical_context():
    project = SimpleNamespace(status=ProjectStatus.ONTOLOGY_GENERATED, graph_id=None)
    with pytest.raises(AgentBacktestError, match="anonymized"):
        plan_agent_backtest(
            ["proj_123"], project_loader=lambda _: project,
            context_loader=lambda _: _context(), dossier_loader=lambda _: DOSSIER,
            max_graphs=1,
        )


def test_sports_prepare_keeps_mirofish_persona_and_config_steps_but_labels_synthetic(
    tmp_path: Path, monkeypatch,
):
    import app.services.simulation_manager as manager_module
    from app.services.oasis_profile_generator import OasisAgentProfile

    monkeypatch.setenv("MIROFISH_SPORTS_AGENT_COUNT", "10")
    monkeypatch.setattr(manager_module.SimulationManager, "SIMULATION_DATA_DIR", str(tmp_path))
    entities = [EntityNode(
        uuid=f"entity-{i}", name=f"Real Player {i}", labels=["Player"],
        summary="A real player in the source graph", attributes={},
    ) for i in range(14)]
    observed = {"personas_called": 0, "config_called": 0}

    class Reader:
        def filter_defined_entities(self, **_):
            return FilteredEntities(entities, {"Player"}, len(entities), len(entities))

    class Personas:
        def __init__(self, graph_id):
            assert graph_id == "graph_1"

        def generate_profiles_from_entities(self, *, entities, **_):
            observed["personas_called"] += 1
            return [OasisAgentProfile(
                user_id=i, user_name=f"real_{i}", name=entity.name,
                bio="Real source entity", persona="Discuss the match",
                source_entity_type="Player",
            ) for i, entity in enumerate(entities)]

        def save_profiles(self, *, profiles, file_path, platform):
            if platform == "reddit":
                Path(file_path).write_text(
                    json.dumps([profile.to_reddit_format() for profile in profiles]),
                    encoding="utf-8",
                )
            else:
                Path(file_path).write_text("synthetic sports profiles", encoding="utf-8")

    class Parameters:
        def __init__(self, entities):
            self.agent_configs = [SimpleNamespace(agent_id=i, entity_name=e.name)
                                  for i, e in enumerate(entities)]
            self.event_config = SimpleNamespace(
                initial_posts=[{"content": "Model-invented event"}],
                scheduled_events=[{"content": "Model-invented future event"}],
                narrative_direction="Model direction",
            )
            self.generation_reasoning = "Ordinary MiroFish config generation ran"

        def to_json(self):
            return json.dumps({
                "agent_configs": [item.__dict__ for item in self.agent_configs],
                "event_config": self.event_config.__dict__,
            })

    class ConfigGenerator:
        def generate_config(self, *, entities, **_):
            observed["config_called"] += 1
            return Parameters(entities)

    monkeypatch.setattr(manager_module, "ZepEntityReader", Reader)
    monkeypatch.setattr(manager_module, "OasisProfileGenerator", Personas)
    monkeypatch.setattr(manager_module, "SimulationConfigGenerator", ConfigGenerator)
    manager = manager_module.SimulationManager()
    simulation = manager.create_simulation("proj_123", "graph_1")
    prepared = manager_module.SimulationManager.prepare_simulation.__wrapped__(
        manager, simulation.simulation_id, "Synthetic pre-match forecast", DOSSIER,
    )
    assert observed == {"personas_called": 1, "config_called": 1}
    assert prepared.profiles_count == 10
    data = json.loads((tmp_path / simulation.simulation_id / "simulation_config.json").read_text())
    assert all(item["entity_name"].startswith("Synthetic Player perspective")
               for item in data["agent_configs"])
    assert data["event_config"]["scheduled_events"] == []
    assert "Pre-match dossier" in data["event_config"]["initial_posts"][0]["content"]
    profile_data = json.loads((tmp_path / simulation.simulation_id / "reddit_profiles.json").read_text())
    assert all(item["name"].startswith("Synthetic Player perspective") for item in profile_data)
