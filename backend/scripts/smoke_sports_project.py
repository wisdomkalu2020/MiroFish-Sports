r"""Exercise one synthetic sports project through the real local ontology route.

This creates temporary, explicitly synthetic API-Sports response captures and
deletes the test project afterward. It makes no provider or Zep calls.

PowerShell, from backend:
    & .\.venv\Scripts\python.exe -m scripts.smoke_sports_project
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app import create_app
from app.models.project import ProjectManager
from app.sports.forecast_context import load_forecast_context
from app.sports.snapshots import SnapshotStore


def main() -> None:
    now = datetime.now(timezone.utc)
    kickoff = now + timedelta(days=7)
    cutoff = kickoff - timedelta(hours=24)
    fixture_id = 9999999
    with tempfile.TemporaryDirectory(prefix="mirofish-sports-smoke-") as directory:
        original_projects = ProjectManager.PROJECTS_DIR
        original_snapshots = os.environ.get("MIROFISH_SPORTS_SNAPSHOT_DIR")
        ProjectManager.PROJECTS_DIR = str(Path(directory) / "projects")
        os.environ["MIROFISH_SPORTS_SNAPSHOT_DIR"] = str(Path(directory) / "snapshots")
        try:
            store = SnapshotStore(Path(directory) / "snapshots")
            base = "https://v3.football.api-sports.io"
            fixture = {
                "fixture": {
                    "id": fixture_id,
                    "date": kickoff.isoformat(),
                    "timestamp": int(kickoff.timestamp()),
                    "status": {"short": "NS"},
                    "venue": {"name": "Synthetic Ground"},
                },
                "league": {"id": 39, "season": kickoff.year, "name": "Premier League"},
                "teams": {
                    "home": {"id": 999001, "name": "Synthetic Home"},
                    "away": {"id": 999002, "name": "Synthetic Away"},
                },
            }
            captures = {
                f"{base}/fixtures?id={fixture_id}": {"response": [fixture], "paging": {"total": 1}},
                f"{base}/fixtures/lineups?fixture={fixture_id}": {"response": [], "paging": {"total": 1}},
                f"{base}/fixtures?league=39&season={kickoff.year}": {"response": [], "paging": {"total": 1}},
            }
            for url, payload in captures.items():
                store.save(url, json.dumps(payload).encode("utf-8"), now)
            response = create_app().test_client().post(
                "/api/sports/dossiers",
                json={
                    "sport": "football", "league": "epl", "match_id": fixture_id,
                    "kickoff_at": kickoff.isoformat(), "cutoff_hours": 24,
                },
            )
            body = response.get_json()
            if response.status_code != 201 or not body.get("success"):
                raise RuntimeError(f"synthetic sports project failed: HTTP {response.status_code}: {body}")
            project_id = body["data"]["project_id"]
            context = load_forecast_context(project_id)
            print(
                "synthetic_sports_project=ok "
                f"ontology_entities={len(body['data']['ontology']['entity_types'])} "
                f"baseline_training_matches={context['training_matches']} "
                f"probability_sum={sum(context['baseline_probabilities'].values()):.6f} "
                f"warnings={len(context['warnings'])}"
            )
        finally:
            ProjectManager.PROJECTS_DIR = original_projects
            if original_snapshots is None:
                os.environ.pop("MIROFISH_SPORTS_SNAPSHOT_DIR", None)
            else:
                os.environ["MIROFISH_SPORTS_SNAPSHOT_DIR"] = original_snapshots


if __name__ == "__main__":
    main()
