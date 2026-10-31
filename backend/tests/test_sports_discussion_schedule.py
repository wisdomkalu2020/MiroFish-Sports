import ast
import json
import random
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.sports.discussion_schedule import apply_sports_discussion_schedule

ROOT = Path(__file__).resolve().parents[1]
DOSSIER = "# Pre-match dossier: A vs B\n- Sport: football\n- Evidence cutoff (UTC): 2026-10-01T00:00:00Z"


def original():
    return {"time_config": {"agents_per_hour_min": 5, "agents_per_hour_max": 6,
            "off_peak_hours": [0, 1, 2, 3, 4, 5], "off_peak_activity_multiplier": 0.05},
            "agent_configs": [{"agent_id": i, "active_hours": list(range(9, 18)),
                               "activity_level": 0.1} for i in range(10)]}


def selector(filename):
    # Execute the actual runner selection function without loading OASIS/LLMs.
    tree = ast.parse((ROOT / "scripts" / filename).read_text(encoding="utf-8-sig"))
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                and n.name in ("_get_active_agents_for_round", "get_active_agents_for_round"))
    module = ast.Module(body=[node], type_ignores=[])
    namespace = {"random": random, "List": list, "Dict": dict, "Any": object}
    exec(compile(module, filename, "exec"), namespace)
    return namespace[node.name]


@pytest.mark.parametrize("filename", ["run_reddit_simulation.py", "run_twitter_simulation.py",
                                     "run_parallel_simulation.py"])
def test_actual_runners_select_every_sports_agent_even_at_midnight(filename):
    fn = selector(filename)
    env = SimpleNamespace(agent_graph=SimpleNamespace(get_agent=lambda i: i))
    before = original()
    fixed = apply_sports_discussion_schedule(before, DOSSIER)
    def choose(config, hour):
        if filename == "run_parallel_simulation.py":
            return fn(env, config, hour, hour)
        return fn(SimpleNamespace(config=config), env, hour, hour)
    assert choose(before, 0) == []
    assert choose(before, 1) == []
    for hour in range(24):
        assert {i for i, _ in choose(fixed, hour)} == set(range(10))
    assert before == original()


def test_generic_schedule_unchanged():
    before = original()
    assert apply_sports_discussion_schedule(before, "Stock-market seed") == before


def test_duplicate_roster_rejected():
    config = original()
    config["agent_configs"][1]["agent_id"] = 0
    with pytest.raises(ValueError):
        apply_sports_discussion_schedule(config, DOSSIER)
