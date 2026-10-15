"""Pre-match sports seeds for MiroFish's existing graph and simulation flow."""

from __future__ import annotations

import io
import os
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from math import isfinite
from pathlib import Path

from flask import jsonify, request
from werkzeug.datastructures import FileStorage

from . import sports_bp
from ..config import Config
from ..models.project import ProjectManager, ProjectStatus
from ..services.ontology_generator import OntologyGenerator
from ..sports.api_sports import (
    ApiSportsFootballAdapter,
    ApiSportsNBAAdapter,
    SportsSourceError,
)
from ..sports.dossier import CutoffViolation, build_match_dossier
from ..sports.forecast_context import build_forecast_context, save_forecast_context
from ..sports.snapshots import SnapshotError, SnapshotStore, SnapshotUnavailable
from ..utils.llm_client import LLMResponseError
from ..utils.local_llm_runtime import local_model_run
from ..utils.logger import get_logger
from ..utils.run_budget import BudgetExceeded


logger = get_logger("mirofish.api.sports")

_SIMULATION_REQUIREMENT = (
    "Explore this fixture in a synthetic pre-match sports discussion using only "
    "the dossier's as-of evidence. Synthetic perspectives are not actual "
    "statements by coaches, players, referees, or teams. Preserve uncertainty "
    "and data-freshness warnings."
)


class _RequestError(ValueError):
    pass


class _KickoffMismatch(ValueError):
    pass


def _parse_request(payload: object) -> tuple[str, str | None, str, datetime, float, datetime]:
    if not isinstance(payload, dict):
        raise _RequestError("A JSON object is required")
    sport = payload.get("sport")
    if sport not in ("football", "basketball"):
        raise _RequestError("sport must be football or basketball")
    league = payload.get("league")
    if sport == "football" and league not in ("epl", "npfl"):
        raise _RequestError("football league must be epl or npfl")
    if sport == "basketball" and league not in (None, ""):
        raise _RequestError("basketball does not accept a football league")
    match_id = payload.get("match_id")
    if isinstance(match_id, bool) or not (
        isinstance(match_id, int)
        or (isinstance(match_id, str) and match_id.isdecimal())
    ):
        raise _RequestError("match_id must be a positive numeric provider ID")
    match_id = str(int(match_id))
    if int(match_id) <= 0:
        raise _RequestError("match_id must be a positive numeric provider ID")
    raw_kickoff = payload.get("kickoff_at")
    if not isinstance(raw_kickoff, str) or not (
        raw_kickoff.endswith("Z") or raw_kickoff.endswith("+00:00")
    ):
        raise _RequestError("kickoff_at must be an explicit UTC ISO-8601 timestamp")
    try:
        kickoff_at = datetime.fromisoformat(raw_kickoff.replace("Z", "+00:00"))
    except ValueError as error:
        raise _RequestError("kickoff_at must be an explicit UTC ISO-8601 timestamp") from error
    if kickoff_at.tzinfo is None or kickoff_at.utcoffset() != timedelta(0):
        raise _RequestError("kickoff_at must be an explicit UTC ISO-8601 timestamp")
    kickoff_at = kickoff_at.astimezone(timezone.utc)
    cutoff_hours = payload.get("cutoff_hours")
    if isinstance(cutoff_hours, bool) or not isinstance(cutoff_hours, (int, float)):
        raise _RequestError("cutoff_hours must be an explicit positive number")
    if not isfinite(cutoff_hours) or cutoff_hours <= 0:
        raise _RequestError("cutoff_hours must be an explicit positive number")
    try:
        cutoff_at = kickoff_at - timedelta(hours=cutoff_hours)
    except OverflowError as error:
        raise _RequestError("cutoff_hours is too large") from error
    return sport, league, match_id, kickoff_at, float(cutoff_hours), cutoff_at


def _snapshot_store() -> SnapshotStore:
    root = os.environ.get("MIROFISH_SPORTS_SNAPSHOT_DIR")
    if not root:
        root = str(Path(Config.UPLOAD_FOLDER) / "sports_snapshots")
    return SnapshotStore(Path(root))


@sports_bp.route("/dossiers", methods=["POST"])
def create_match_dossier():
    """Create an ordinary ontology-ready project from an as-of match dossier.

    The existing ``/api/graph/build`` endpoint and OASIS simulation path
    consume this project without any new graph or memory implementation.
    """

    try:
        sport, league, match_id, kickoff_at, cutoff_hours, cutoff_at = _parse_request(
            request.get_json(silent=True)
        )
    except _RequestError as error:
        return jsonify({"success": False, "error": str(error)}), 400

    project = None
    try:
        snapshot_store = _snapshot_store()
        adapter = (
            ApiSportsFootballAdapter(league, snapshot_store, include_lineups=True)
            if sport == "football"
            else ApiSportsNBAAdapter(snapshot_store)
        )
        match = adapter.load_match(match_id, cutoff_at=cutoff_at)
        if match.fixture.match_id != match_id or match.fixture.sport != sport:
            raise SportsSourceError("The provider returned a different match or sport")
        if match.fixture.kickoff_at != kickoff_at:
            raise _KickoffMismatch(
                "The pre-cutoff fixture kickoff differs from the requested kickoff_at"
            )
        dossier = build_match_dossier(
            match.fixture, match.facts, cutoff_hours=cutoff_hours
        )
        markdown = dossier.to_markdown()

        project = ProjectManager.create_project(
            name=f"{match.fixture.home_team} vs {match.fixture.away_team} ({match.fixture.competition})"
        )
        project.simulation_requirement = _SIMULATION_REQUIREMENT
        filename = "match-dossier.md"
        source_file = FileStorage(
            stream=io.BytesIO(markdown.encode("utf-8")),
            filename=filename,
            content_type="text/markdown",
        )
        file_info = ProjectManager.save_file_to_project(
            project.project_id, source_file, filename
        )
        project.files.append(
            {"filename": file_info["original_filename"], "size": file_info["size"]}
        )
        project.total_text_length = len(markdown)
        ProjectManager.save_extracted_text(project.project_id, markdown)

        forecast_context = build_forecast_context(
            project_id=project.project_id,
            fixture=match.fixture,
            league=league or "nba",
            cutoff_at=cutoff_at,
            cutoff_hours=cutoff_hours,
            dossier_markdown=markdown,
        )
        save_forecast_context(project.project_id, forecast_context)

        scope = (
            local_model_run(project.project_id)
            if Config.LLM_PROVIDER == "local"
            else nullcontext()
        )
        with scope:
            ontology = OntologyGenerator().generate(
                document_texts=[markdown],
                simulation_requirement=_SIMULATION_REQUIREMENT,
                domain="sports",
            )
        project.ontology = {
            "entity_types": ontology.get("entity_types", []),
            "edge_types": ontology.get("edge_types", []),
        }
        project.analysis_summary = ontology.get("analysis_summary", "")
        project.status = ProjectStatus.ONTOLOGY_GENERATED
        ProjectManager.save_project(project)
        return jsonify(
            {
                "success": True,
                "data": {
                    "project_id": project.project_id,
                    "project_name": project.name,
                    "sport": sport,
                    "league": league,
                    "match_id": match_id,
                    "kickoff_at": kickoff_at.isoformat(),
                    "cutoff_at": cutoff_at.isoformat(),
                    "cutoff_hours": cutoff_hours,
                    "kickoff_unverified": match.fixture.kickoff_unverified,
                    "kickoff_stale": match.fixture.kickoff_stale,
                    "excluded_fact_count": dossier.excluded_fact_count,
                    "dossier": markdown,
                    "ontology": project.ontology,
                    "analysis_summary": project.analysis_summary,
                    "baseline_method": forecast_context["baseline_method"],
                    "baseline_probabilities": forecast_context["baseline_probabilities"],
                    "baseline_training_matches": forecast_context["training_matches"],
                    "baseline_warnings": forecast_context["warnings"],
                },
            }
        ), 201
    except (_KickoffMismatch, CutoffViolation) as error:
        status, public_error = 409, str(error)
    except SnapshotUnavailable:
        status, public_error = 409, "No sports source snapshot was captured by the requested cutoff"
    except BudgetExceeded:
        status, public_error = 429, "Local model run budget exhausted"
    except (SportsSourceError, SnapshotError, LLMResponseError):
        logger.exception("Sports dossier source or ontology failed")
        status, public_error = 502, "Sports source or ontology generation failed; check server logs"
    except Exception:
        logger.exception("Unexpected sports dossier creation failure")
        status, public_error = 500, "Sports dossier creation failed; check server logs"

    if project is not None:
        try:
            ProjectManager.delete_project(project.project_id)
        except Exception:
            logger.exception("Could not remove incomplete sports project %s", project.project_id)
    return jsonify({"success": False, "error": public_error}), status
