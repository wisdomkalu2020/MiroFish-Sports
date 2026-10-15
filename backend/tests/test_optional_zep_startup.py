"""Offline sports pages can start without weakening Zep graph checks."""

from app import create_app
from app.config import Config


def test_no_zep_key_allows_offline_app_but_refuses_graph(monkeypatch):
    monkeypatch.setattr(Config, "LLM_PROVIDER", "local")
    monkeypatch.setattr(Config, "LLM_MODEL_NAME", "phi4-mini")
    monkeypatch.setattr(Config, "ZEP_API_KEY", None)
    monkeypatch.setattr(Config, "DEBUG", False)
    monkeypatch.delenv("ZEP_API_URL", raising=False)

    assert Config.validate() == []
    client = create_app().test_client()
    leagues = client.get("/api/sports/evaluation/leagues")
    assert leagues.status_code == 200

    graph = client.post("/api/graph/build", json={"project_id": "proj_example"})
    assert graph.status_code == 500
    assert graph.get_json()["success"] is False
