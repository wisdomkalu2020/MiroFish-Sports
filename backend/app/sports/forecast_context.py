"""An immutable statistical starting point for an existing sports project.

The context lives beside MiroFish's normal extracted dossier.  It does not
replace the graph, personas, OASIS simulation, ReportAgent, or Zep memory.
Only imported finals captured by the fixture cutoff may train a live forecast.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from math import isclose, isfinite
from pathlib import Path

from app.models.project import ProjectManager

from .baseline import BasketballBaseline, FootballBaseline
from .history import HistoricalMatch
from .history_store import default_history_root, load_history
from .schema import MatchFixture


class ForecastContextError(ValueError):
    """The project lacks a valid, immutable sports forecast context."""


_FILENAME = "sports_forecast_context.json"


def _utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def build_forecast_context(
    *,
    project_id: str,
    fixture: MatchFixture,
    league: str,
    cutoff_at: datetime,
    cutoff_hours: float,
    dossier_markdown: str,
    history: tuple[HistoricalMatch, ...] | None = None,
) -> dict:
    """Train from captured, available finals and forecast one future fixture.

    ``history`` is injectable for focused tests.  The default reads MiroFish's
    immutable sports-history store.  A retrospective file fetched after a
    past cutoff is intentionally excluded from a live project forecast.
    """

    if not isinstance(project_id, str) or not project_id.startswith("proj_"):
        raise ValueError("project_id must be a MiroFish project ID")
    if not isinstance(fixture, MatchFixture):
        raise TypeError("fixture must be a MatchFixture")
    cutoff_at = _utc(cutoff_at, "cutoff_at")
    if cutoff_at >= fixture.kickoff_at:
        raise ValueError("forecast cutoff must precede kickoff")
    if not isinstance(cutoff_hours, (int, float)) or isinstance(cutoff_hours, bool) or not isfinite(cutoff_hours) or cutoff_hours <= 0:
        raise ValueError("cutoff_hours must be a positive finite number")
    if not isinstance(dossier_markdown, str) or not dossier_markdown:
        raise ValueError("dossier markdown is required")
    expected_league = {"epl": "EPL", "npfl": "NPFL", "nba": "NBA"}.get(league.lower())
    if expected_league is None or (fixture.sport == "basketball") != (expected_league == "NBA"):
        raise ValueError("league does not match fixture sport")
    candidates = (
        history if history is not None
        else load_history(default_history_root(), sport=fixture.sport, league=expected_league)
    )
    known = tuple(sorted(
        (
            row for row in candidates
            if row.sport == fixture.sport
            and row.league == expected_league
            and row.match_id != fixture.match_id
            and row.result_available_at <= cutoff_at
            and row.fetched_at <= cutoff_at
        ),
        key=lambda row: (row.result_available_at, row.match_id),
    ))
    model = FootballBaseline(expected_league) if fixture.sport == "football" else BasketballBaseline(expected_league)
    for row in known:
        model.observe(row, as_of=cutoff_at)
    forecast = model.predict(fixture.home_team, fixture.away_team)
    if fixture.sport == "football":
        probabilities = dict(forecast.combined)
        method = "equal_weight_football_elo_dixon_coles"
        scorelines = dict(forecast.scorelines)
        components = {"elo": dict(forecast.elo), "dixon_coles": dict(forecast.dixon_coles)}
    else:
        probabilities = dict(forecast.elo)
        method = "basketball_elo"
        scorelines = None
        components = {"elo": dict(forecast.elo)}
    warnings = list(fixture.data_warnings)
    if fixture.kickoff_unverified:
        warnings.append("Fixture kickoff has not been independently verified")
    if fixture.kickoff_stale:
        warnings.append("Fixture kickoff may be stale")
    if not known:
        warnings.append("No imported final-score history was captured by this cutoff; baseline uses cold-start priors")
    elif fixture.home_team not in model.elo.ratings or fixture.away_team not in model.elo.ratings:
        warnings.append("At least one team has no exact-name match in captured history; check provider naming")
    return {
        "schema_version": 1,
        "project_id": project_id,
        "sport": fixture.sport,
        "league": expected_league,
        "match_id": fixture.match_id,
        "home_team": fixture.home_team,
        "away_team": fixture.away_team,
        "kickoff_at": fixture.kickoff_at.isoformat(),
        "cutoff_at": cutoff_at.isoformat(),
        "cutoff_hours": float(cutoff_hours),
        "dossier_sha256": hashlib.sha256(dossier_markdown.encode("utf-8")).hexdigest(),
        "baseline_method": method,
        "baseline_probabilities": probabilities,
        "baseline_components": components,
        "baseline_scorelines": scorelines,
        "training_matches": len(known),
        "training_match_ids": [row.match_id for row in known],
        "training_sources": sorted({row.source for row in known}),
        "training_latest_fetch_at": max((row.fetched_at for row in known), default=None).isoformat() if known else None,
        "warnings": warnings,
    }


def save_forecast_context(project_id: str, context: dict) -> Path:
    """Create once; changing a forecast requires a new MiroFish project."""

    if context.get("project_id") != project_id:
        raise ForecastContextError("forecast project ID mismatch")
    path = Path(ProjectManager._get_project_dir(project_id)) / _FILENAME
    if not path.parent.is_dir():
        raise ForecastContextError("MiroFish project directory is missing")
    try:
        with path.open("x", encoding="utf-8") as stream:
            json.dump(context, stream, sort_keys=True, indent=2, ensure_ascii=False)
            stream.write("\n")
    except FileExistsError as error:
        raise ForecastContextError("sports forecast context already exists") from error
    return path


def load_forecast_context(project_id: str) -> dict:
    """Read and verify the dossier binding and final probability simplex."""

    if not isinstance(project_id, str) or not project_id.startswith("proj_") or any(character in project_id for character in ("/", "\\", ".")):
        raise ForecastContextError("invalid MiroFish project ID")
    path = Path(ProjectManager._get_project_dir(project_id)) / _FILENAME
    try:
        context = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ForecastContextError("sports forecast context is missing or invalid") from error
    if not isinstance(context, dict) or context.get("schema_version") != 1 or context.get("project_id") != project_id:
        raise ForecastContextError("sports forecast context schema or project ID mismatch")
    dossier = ProjectManager.get_extracted_text(project_id)
    if not isinstance(dossier, str) or hashlib.sha256(dossier.encode("utf-8")).hexdigest() != context.get("dossier_sha256"):
        raise ForecastContextError("sports dossier changed after forecast creation")
    probabilities = context.get("baseline_probabilities")
    outcomes = {"home", "draw", "away"} if context.get("sport") == "football" else {"home", "away"} if context.get("sport") == "basketball" else set()
    if not isinstance(probabilities, dict) or set(probabilities) != outcomes:
        raise ForecastContextError("sports baseline outcomes are invalid")
    if any(type(value) not in (int, float) or not isfinite(value) or value < 0 or value > 1 for value in probabilities.values()) or not isclose(sum(probabilities.values()), 1.0, abs_tol=1e-8, rel_tol=0):
        raise ForecastContextError("sports baseline probabilities are invalid")
    return context
