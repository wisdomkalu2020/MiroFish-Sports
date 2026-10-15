"""Sports evaluation stays inside MiroFish's API and immutable history store."""

import io

from app import create_app


SOURCE = "https://raw.githubusercontent.com/openfootball/england/master/2024-25/1-premierleague.txt"
SCORES = b"Thu Aug 15 2024\nA v B 2-1\nSun Aug 18 2024\nC v D 0-0\n"


def _import(client, scores=SCORES):
    return client.post(
        "/api/sports/evaluation/import",
        data={
            "sport": "football", "league": "EPL", "season": "2024-25",
            "source": SOURCE, "file": (io.BytesIO(scores), "premierleague.txt"),
        },
        content_type="multipart/form-data",
    )


def test_import_and_run_holdout_without_agents_or_zep(tmp_path, monkeypatch):
    monkeypatch.setenv("MIROFISH_SPORTS_HISTORY_DIR", str(tmp_path / "history"))
    client = create_app().test_client()
    initial = client.get("/api/sports/evaluation/leagues")
    assert initial.status_code == 200
    assert next(item for item in initial.json["data"]["leagues"] if item["league"] == "EPL")["match_count"] == 0

    imported = _import(client)
    assert imported.status_code == 201
    assert imported.json["data"]["imported_match_count"] == 2
    assert imported.json["data"]["snapshot_id"].startswith("history-")
    assert imported.json["data"]["note"]
    listed = client.get("/api/sports/evaluation/leagues")
    epl = next(item for item in listed.json["data"]["leagues"] if item["league"] == "EPL")
    assert epl["match_count"] == 2
    assert epl["seasons"] == ["2024-25"]

    evaluated = client.post("/api/sports/evaluation/run", json={
        "sport": "football", "league": "EPL", "cutoff_hours": 24,
        "evaluation_start": "2024-08-18",
    })
    assert evaluated.status_code == 200
    report = evaluated.json["data"]
    assert report["match_count"] == 1
    assert report["history_match_count"] == 2
    assert report["variants"]["naive"]["count"] == 1
    assert report["variants"]["baseline_plus_agents"]["status"] == "unavailable"
    assert report["variants"]["bookmaker"]["status"] == "unavailable"
    assert "predictions" not in report


def test_conflicting_import_does_not_replace_existing_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("MIROFISH_SPORTS_HISTORY_DIR", str(tmp_path / "history"))
    client = create_app().test_client()
    assert _import(client).status_code == 201
    changed = _import(client, SCORES.replace(b"2-1", b"9-0"))
    assert changed.status_code == 400
    listed = client.get("/api/sports/evaluation/leagues")
    epl = next(item for item in listed.json["data"]["leagues"] if item["league"] == "EPL")
    assert epl["match_count"] == 2


def test_run_rejects_missing_history_and_bad_cutoff(tmp_path, monkeypatch):
    monkeypatch.setenv("MIROFISH_SPORTS_HISTORY_DIR", str(tmp_path / "history"))
    client = create_app().test_client()
    missing = client.post("/api/sports/evaluation/run", json={
        "sport": "basketball", "league": "NBA", "cutoff_hours": 24,
    })
    assert missing.status_code == 409
    invalid = client.post("/api/sports/evaluation/run", json={
        "sport": "football", "league": "EPL", "cutoff_hours": 0,
    })
    assert invalid.status_code == 400
