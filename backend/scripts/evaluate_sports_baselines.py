r"""Offline walk-forward baseline evaluation for one sports league.

Examples (PowerShell, from ``backend``):

  .\.venv\Scripts\python.exe -m scripts.evaluate_sports_baselines `
    --football-file EPL 2024-25 C:\data\premierleague.txt https://example/source.txt 2026-10-01T00:00:00Z `
    --cutoff-hours 24 --output C:\data\epl-evaluation.json

  .\.venv\Scripts\python.exe -m scripts.evaluate_sports_baselines `
    --nba-file 2025 C:\data\nba_schedule_2025.csv https://example/source.csv 2026-10-01T00:00:00Z `
    --cutoff-hours 24 --output C:\data\nba-evaluation.json

Pass multiple seasons with repeated ``--football-file`` or ``--nba-file``.
Source URL and fetched time are mandatory provenance, not automatic downloads.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path

from app.sports.evaluation import AgentPrediction, MarketOdds, evaluate_walk_forward
from app.sports.history import load_nba_schedule_file, load_openfootball_file


def _timestamp(raw: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, TypeError) as error:
        raise argparse.ArgumentTypeError("timestamp must be ISO 8601 with a timezone") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamp must include a timezone")
    return parsed


def _date(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError as error:
        raise argparse.ArgumentTypeError("date must be YYYY-MM-DD") from error


def _external_json(path: str) -> list[dict]:
    source = Path(path)
    if source.stat().st_size > 20_000_000:
        raise ValueError("external prediction file exceeds 20 MB")
    rows = json.loads(source.read_text(encoding="utf-8-sig"))
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("external prediction file must contain a JSON array of objects")
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument(
        "--football-file", nargs=5, action="append",
        metavar=("LEAGUE", "SEASON", "PATH", "SOURCE_URL", "FETCHED_AT"),
        help="repeat for seasons of one league; LEAGUE is EPL or NPFL",
    )
    source_group.add_argument(
        "--nba-file", nargs=4, action="append",
        metavar=("END_YEAR", "PATH", "SOURCE_URL", "FETCHED_AT"),
        help="repeat for NBA seasons",
    )
    parser.add_argument("--cutoff-hours", type=float, required=True)
    parser.add_argument("--evaluate-from", type=_date, help="score only matches on/after YYYY-MM-DD; earlier finals warm models as available")
    parser.add_argument("--agents-json", help="optional captured agent-final probability rows")
    parser.add_argument("--odds-json", help="optional captured bookmaker decimal-odds rows")
    parser.add_argument("--output", help="write complete JSON report, including per-match probabilities")
    args = parser.parse_args(argv)
    try:
        history = []
        if args.football_file:
            for league, season, path, source, fetched in args.football_file:
                history.extend(load_openfootball_file(
                    path, league=league, season=season, source=source, fetched_at=_timestamp(fetched),
                ))
        else:
            for year, path, source, fetched in args.nba_file:
                history.extend(load_nba_schedule_file(
                    path, season_end_year=int(year), source=source, fetched_at=_timestamp(fetched),
                ))
        agents = []
        if args.agents_json:
            for row in _external_json(args.agents_json):
                agents.append(AgentPrediction(
                    match_id=row["match_id"],
                    recorded_at=_timestamp(row["recorded_at"]),
                    probabilities=row["probabilities"],
                    leakage_method=row["leakage_method"],
                    model_training_cutoff_at=(
                        _timestamp(row["model_training_cutoff_at"])
                        if row.get("model_training_cutoff_at") else None
                    ),
                ))
        odds = []
        if args.odds_json:
            for row in _external_json(args.odds_json):
                odds.append(MarketOdds(
                    match_id=row["match_id"],
                    recorded_at=_timestamp(row["recorded_at"]),
                    decimal_odds=row["decimal_odds"],
                    source=row["source"],
                ))
        report = evaluate_walk_forward(
            history, cutoff_hours=args.cutoff_hours,
            evaluation_start=args.evaluate_from,
            agent_predictions=agents, market_odds=odds,
        )
    except (OSError, KeyError, TypeError, ValueError) as error:
        parser.error(str(error))
    if args.output:
        Path(args.output).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    summary = {key: value for key, value in report.items() if key != "predictions"}
    summary["output"] = args.output or None
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
