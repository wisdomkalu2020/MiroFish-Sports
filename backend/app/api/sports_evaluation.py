"""Offline historical import and walk-forward evaluation in MiroFish.

These endpoints do not invoke an LLM, OASIS, or Zep.  Uploaded source bytes
are parsed into the existing immutable sports-history store; evaluation then
reads only that store.  No user-supplied filesystem path is accepted.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from math import isfinite

from flask import Blueprint, jsonify, request

from ..sports.evaluation import evaluate_walk_forward
from ..sports.history import HistoryParseError, parse_nba_schedule_csv, parse_openfootball_txt
from ..sports.history_store import HistoryStoreError, default_history_root, load_history, save_history
from ..utils.logger import get_logger


sports_evaluation_bp = Blueprint("sports_evaluation", __name__)
logger = get_logger("mirofish.api.sports_evaluation")
_MAX_SOURCE_BYTES = 20_000_000
_LEAGUES = (("football", "EPL"), ("football", "NPFL"), ("basketball", "NBA"))


def _selection(sport: object, league: object) -> tuple[str, str]:
    if not isinstance(sport, str) or not isinstance(league, str):
        raise ValueError("sport and league are required")
    selected = (sport.strip().lower(), league.strip().upper())
    if selected not in _LEAGUES:
        raise ValueError("choose EPL, NPFL, or NBA with its matching sport")
    return selected


def _bad_request(error: Exception, status: int = 400):
    return jsonify({"success": False, "error": str(error)}), status


@sports_evaluation_bp.get("/leagues")
def list_imported_leagues():
    """Show import counts; an empty store is a normal first-run state."""

    try:
        root = default_history_root()
        leagues = []
        for sport, league in _LEAGUES:
            matches = load_history(root, sport=sport, league=league)
            leagues.append({
                "sport": sport,
                "league": league,
                "match_count": len(matches),
                "seasons": sorted({match.season for match in matches}),
                "first_match_date": min((match.match_date.isoformat() for match in matches), default=None),
                "last_match_date": max((match.match_date.isoformat() for match in matches), default=None),
            })
        return jsonify({"success": True, "data": {"leagues": leagues}})
    except (HistoryStoreError, OSError):
        logger.exception("Could not read sports historical snapshots")
        return _bad_request(ValueError("Historical snapshots could not be read; check server logs"), 500)


@sports_evaluation_bp.post("/import")
def import_historical_scores():
    """Import a supplied historical source file; never fetch it implicitly."""

    try:
        sport, league = _selection(request.form.get("sport"), request.form.get("league"))
        season = request.form.get("season", "").strip()
        source = request.form.get("source", "").strip()
        if not season or not source:
            raise ValueError("season and source URL are required")
        uploaded = request.files.get("file")
        if uploaded is None:
            raise ValueError("a source file is required")
        raw = uploaded.stream.read(_MAX_SOURCE_BYTES + 1)
        if len(raw) > _MAX_SOURCE_BYTES:
            raise ValueError("source file exceeds 20 MB")
        try:
            source_text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise ValueError("source file must be UTF-8") from error
        received_at = datetime.now(timezone.utc)
        if sport == "football":
            records = parse_openfootball_txt(
                source_text, league=league, season=season,
                source=source, fetched_at=received_at,
            )
        else:
            try:
                season_end_year = int(season)
            except ValueError as error:
                raise ValueError("NBA season must be its four-digit ending year") from error
            records = parse_nba_schedule_csv(
                source_text, season_end_year=season_end_year,
                source=source, fetched_at=received_at,
            )
        snapshot = save_history(default_history_root(), records)
        return jsonify({
            "success": True,
            "data": {
                "sport": sport,
                "league": league,
                "season": season,
                "imported_match_count": len(records),
                "snapshot_id": snapshot.name,
                "received_at": received_at.isoformat(),
                "note": "This import does not establish archived pre-match dossier evidence.",
            },
        }), 201
    except (HistoryParseError, HistoryStoreError, ValueError) as error:
        return _bad_request(error)
    except OSError:
        logger.exception("Could not store sports historical scores")
        return _bad_request(ValueError("Historical import could not be saved; check server logs"), 500)


@sports_evaluation_bp.post("/run")
def run_historical_evaluation():
    """Return honest aggregate metrics for a selected imported league."""

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _bad_request(ValueError("a JSON object is required"))
    try:
        sport, league = _selection(payload.get("sport"), payload.get("league"))
        hours = payload.get("cutoff_hours")
        if isinstance(hours, bool) or not isinstance(hours, (int, float)) or not isfinite(hours) or hours <= 0:
            raise ValueError("cutoff_hours must be a positive finite number")
        raw_start = payload.get("evaluation_start")
        if raw_start in (None, ""):
            start = None
        elif isinstance(raw_start, str):
            try:
                start = date.fromisoformat(raw_start)
            except ValueError as error:
                raise ValueError("evaluation_start must be YYYY-MM-DD") from error
        else:
            raise ValueError("evaluation_start must be YYYY-MM-DD")
        matches = load_history(default_history_root(), sport=sport, league=league)
        if not matches:
            return _bad_request(ValueError("No imported final scores for this league"), 409)
        report = evaluate_walk_forward(matches, cutoff_hours=hours, evaluation_start=start)
        # Per-match probabilities remain available through the offline CLI.
        # Aggregate API responses stay small for a full league history.
        report.pop("predictions")
        return jsonify({"success": True, "data": report})
    except (ValueError, HistoryStoreError) as error:
        return _bad_request(error)
    except OSError:
        logger.exception("Could not read sports historical scores")
        return _bad_request(ValueError("Historical evaluation could not read snapshots; check server logs"), 500)
