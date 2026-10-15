r"""Import local final-score files into MiroFish's immutable history store.

PowerShell examples (run from ``backend``):

  .\.venv\Scripts\python.exe -m scripts.import_sports_history `
    --football-file EPL 2025-26 C:\data\premierleague.txt `
    https://raw.githubusercontent.com/openfootball/england/master/2025-26/1-premierleague.txt `
    2026-10-14T12:00:00Z

  .\.venv\Scripts\python.exe -m scripts.import_sports_history `
    --nba-file 2026 C:\data\nba_schedule_2026.csv `
    https://github.com/sportsdataverse/sportsdataverse-data/releases/download/espn_nba_schedules/nba_schedule_2026.csv `
    2026-10-14T12:00:00Z

The source URL and fetched time describe the local file's provenance. This
command never downloads it. A date-only result is held back through the next
UTC day by the existing parsers. Corrections to imported finals require
explicit human resolution: conflicting same-ID scores are rejected.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from app.sports.history import load_nba_schedule_file, load_openfootball_file
from app.sports.history_store import default_history_root, save_history


def _timestamp(raw: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise argparse.ArgumentTypeError("FETCHED_AT must be ISO 8601 with a timezone") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("FETCHED_AT must include a timezone")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument(
        "--football-file", nargs=5,
        metavar=("LEAGUE", "SEASON", "PATH", "SOURCE_URL", "FETCHED_AT"),
        help="OpenFootball TXT, for EPL or NPFL",
    )
    source_group.add_argument(
        "--nba-file", nargs=4,
        metavar=("END_YEAR", "PATH", "SOURCE_URL", "FETCHED_AT"),
        help="SportsDataverse hoopR NBA schedule CSV",
    )
    parser.add_argument(
        "--history-dir", type=Path, default=default_history_root(),
        help="store directory; defaults to MIROFISH_SPORTS_HISTORY_DIR or uploads/sports_history",
    )
    parser.add_argument(
        "--rollover-month", type=int, default=7,
        help="first month of a football season when date headers omit the year",
    )
    args = parser.parse_args(argv)
    try:
        if args.football_file:
            league, season, path, source, fetched = args.football_file
            records = load_openfootball_file(
                path, league=league, season=season, source=source,
                fetched_at=_timestamp(fetched), rollover_month=args.rollover_month,
            )
        else:
            year, path, source, fetched = args.nba_file
            records = load_nba_schedule_file(
                path, season_end_year=int(year), source=source,
                fetched_at=_timestamp(fetched),
            )
        snapshot = save_history(args.history_dir, records)
    except (OSError, ValueError, TypeError, argparse.ArgumentTypeError) as error:
        parser.error(str(error))
    print(json.dumps({
        "snapshot": str(snapshot),
        "records": len(records),
        "sport": records[0].sport,
        "league": records[0].league,
        "season": records[0].season,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
