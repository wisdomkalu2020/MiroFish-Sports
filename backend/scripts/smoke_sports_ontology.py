r"""Test the local sports ontology path without an API-Sports or Zep key.

PowerShell, from backend:
    & .\.venv\Scripts\python.exe -m scripts.smoke_sports_ontology
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.services.ontology_generator import OntologyGenerator
from app.sports import FactCategory, MatchFixture, Provenance, SportsFact, build_match_dossier
from app.utils.local_llm_runtime import local_model_run


def main() -> None:
    now = datetime.now(timezone.utc)
    kickoff = now + timedelta(days=7)
    source = Provenance(source="local synthetic smoke fixture", fetched_at=now)
    fixture = MatchFixture(
        match_id="synthetic-1",
        sport="football",
        competition="Test League",
        season="2026",
        kickoff_at=kickoff,
        home_team="Home Club",
        away_team="Away Club",
        venue="Test Stadium",
        provenance=source,
        kickoff_unverified=True,
    )
    facts = (
        SportsFact(FactCategory.TEAM, "Home Club", "Scheduled home team", source),
        SportsFact(FactCategory.TEAM, "Away Club", "Scheduled away team", source),
        SportsFact(FactCategory.VENUE, "Test Stadium", "Reported venue", source),
    )
    dossier = build_match_dossier(fixture, facts, cutoff_hours=6).to_markdown()
    run_id = f"sim_ontology_smoke_{uuid.uuid4().hex[:10]}"
    with local_model_run(run_id) as gateway:
        ontology = OntologyGenerator().generate(
            document_texts=[dossier],
            simulation_requirement="Synthetic pre-match discussion; no real person statements.",
            domain="sports",
        )
        usage = gateway.budget.snapshot()
    print(
        f"sports_ontology_entities={len(ontology['entity_types'])} "
        f"edges={len(ontology['edge_types'])} "
        f"tokens={usage['spent_tokens']}/{usage['token_limit']}"
    )


if __name__ == "__main__":
    main()
