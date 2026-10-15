"""Historical final scores for statistical training, kept apart from dossiers.

These parsers never call a network service.  Callers supply the exact source
bytes, its URL, and the time those bytes were fetched.  Source files can be
edited retrospectively, so a final score's conservative availability time is
an assumption for the *statistical* history only; it is not proof that a
pre-match dossier fact was captured at that time.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Iterable


OPENFOOTBALL_LICENSE = "https://github.com/openfootball/world/blob/master/LICENSE.md"
SPORTSDATAVERSE_LICENSE = "https://github.com/sportsdataverse/hoopR-nba-data/blob/main/LICENSE.md"
_OPENFOOTBALL_EPL = "https://raw.githubusercontent.com/openfootball/england/master/{season}/1-premierleague.txt"
_OPENFOOTBALL_NPFL = "https://raw.githubusercontent.com/openfootball/world/master/africa/nigeria/{season}_ng1.txt"
_NBA_SCHEDULE = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/espn_nba_schedules/nba_schedule_{season}.csv"
_MAX_SOURCE_BYTES = 20_000_000


class HistoryParseError(ValueError):
    """The source format changed or contains contradictory final results."""


def _utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    return " ".join(value.split())


def conservative_result_available_at(match_date: date) -> datetime:
    """End of the next UTC calendar day for sources without a final timestamp.

    A match listed only by local date can run past UTC midnight (especially an
    NBA game).  Waiting until 00:00 UTC two days later excludes both its own
    date and the following day from pre-match historical features.
    """

    if type(match_date) is not date:
        raise TypeError("match_date must be a date")
    return datetime.combine(match_date + timedelta(days=2), time.min, timezone.utc)


@dataclass(frozen=True, slots=True)
class HistoricalMatch:
    """One verified final; no fixture or final-score claims beyond the source."""

    match_id: str
    sport: str
    league: str
    season: str
    home_team: str
    away_team: str
    match_date: date
    kickoff_at: datetime | None
    kickoff_unverified: bool
    home_score: int
    away_score: int
    result_available_at: datetime
    source: str
    fetched_at: datetime

    def __post_init__(self) -> None:
        for name in ("match_id", "sport", "league", "season", "home_team", "away_team", "source"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        object.__setattr__(self, "sport", self.sport.lower())
        if self.sport not in {"football", "basketball"}:
            raise ValueError("sport must be football or basketball")
        if self.home_team.casefold() == self.away_team.casefold():
            raise ValueError("home_team and away_team must differ")
        if type(self.match_date) is not date:
            raise TypeError("match_date must be a date")
        if not isinstance(self.kickoff_unverified, bool):
            raise TypeError("kickoff_unverified must be a boolean")
        if self.kickoff_at is None:
            if not self.kickoff_unverified:
                raise ValueError("an unknown kickoff must be marked unverified")
        else:
            object.__setattr__(self, "kickoff_at", _utc(self.kickoff_at, "kickoff_at"))
        for name in ("home_score", "away_score"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.sport == "basketball" and self.home_score == self.away_score:
            raise ValueError("a final basketball score cannot be tied")
        object.__setattr__(
            self, "result_available_at", _utc(self.result_available_at, "result_available_at")
        )
        object.__setattr__(self, "fetched_at", _utc(self.fetched_at, "fetched_at"))
        if self.kickoff_at is None:
            if self.result_available_at < conservative_result_available_at(self.match_date):
                raise ValueError("date-only result availability is too early")
        elif self.result_available_at <= self.kickoff_at:
            raise ValueError("result must become available after kickoff")
        if self.fetched_at < self.result_available_at:
            raise ValueError("final result cannot be fetched before its conservative availability time")


def results_known_by(matches: Iterable[HistoricalMatch], cutoff_at: datetime) -> tuple[HistoricalMatch, ...]:
    """Exclude same-day and later results from statistical pre-match history.

    This is intentionally separate from dossier provenance, which requires an
    actual pre-cutoff snapshot for mutable facts such as injuries and lineups.
    """

    cutoff_at = _utc(cutoff_at, "cutoff_at")
    return tuple(match for match in matches if match.result_available_at <= cutoff_at)


def openfootball_source_url(league: str, season: str) -> str:
    """Current mutable upstream URL; capture the returned bytes when used."""

    _season_years(season)
    urls = {"EPL": _OPENFOOTBALL_EPL, "NPFL": _OPENFOOTBALL_NPFL}
    if league not in urls:
        raise ValueError("OpenFootball league must be EPL or NPFL")
    return urls[league].format(season=season)


def nba_schedule_source_url(season_end_year: int) -> str:
    if type(season_end_year) is not int or not 2002 <= season_end_year <= 2100:
        raise ValueError("NBA season_end_year must be an integer from 2002 through 2100")
    return _NBA_SCHEDULE.format(season=season_end_year)


def _season_years(season: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d{4})[-/](\d{2}|\d{4})", season)
    if not match:
        raise ValueError("football season must be YYYY-YY or YYYY/YYYY")
    start = int(match.group(1))
    end = int(match.group(2))
    if end < 100:
        end = (start // 100) * 100 + end
        if end < start:
            end += 100
    if end != start + 1:
        raise ValueError("football season must span one year")
    return start, end


_DATE_WORDS = re.compile(
    r"^(?:(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+)?"
    r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2})(?:\s+(\d{4}))?$",
    re.IGNORECASE,
)
_DATE_NUMBERS = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(?:(\d{4}))?$")
_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_TIME_PREFIX = re.compile(r"^\d{1,2}:\d{2}\s+")
_V_SCORE_END = re.compile(
    r"^(?P<home>.+?)\s+v\s+(?P<away>.+?)\s+"
    r"(?P<home_score>\d+)-(?P<away_score>\d+)"
    r"(?:\s+\(\d+-\d+\))?$",
    re.IGNORECASE,
)
_SCORE_MIDDLE = re.compile(
    r"^(?P<home>.+?)\s+(?P<home_score>\d+)-(?P<away_score>\d+)"
    r"(?:\s+\(\d+-\d+\))?\s+(?P<away>.+)$"
)


def _football_date(line: str, start_year: int, end_year: int, *, rollover_month: int) -> date | None:
    words = _DATE_WORDS.fullmatch(line)
    numbers = _DATE_NUMBERS.fullmatch(line)
    if words:
        month = _MONTHS[words.group(1).lower()]
        day = int(words.group(2))
        explicit_year = words.group(3)
    elif numbers:
        day, month = int(numbers.group(1)), int(numbers.group(2))
        explicit_year = numbers.group(3)
    else:
        return None
    year = int(explicit_year) if explicit_year else (start_year if month >= rollover_month else end_year)
    if year not in (start_year, end_year):
        raise HistoryParseError(f"match date year {year} falls outside season")
    try:
        return date(year, month, day)
    except ValueError as error:
        raise HistoryParseError(f"invalid OpenFootball date {line!r}") from error


def _football_id(league: str, season: str, match_date: date, home: str, away: str) -> str:
    key = "\0".join((league, season, match_date.isoformat(), home.casefold(), away.casefold()))
    return f"{league.lower()}:{season}:{hashlib.sha256(key.encode('utf-8')).hexdigest()[:20]}"


def parse_openfootball_txt(
    raw_text: str,
    *,
    league: str,
    season: str,
    source: str,
    fetched_at: datetime,
    rollover_month: int = 7,
) -> tuple[HistoricalMatch, ...]:
    """Parse OpenFootball EPL/NPFL Football.TXT completed league games.

    Explicit date years take precedence.  Undated-year headers use the
    July-June season convention; callers must override ``rollover_month`` if
    a particular season used another boundary.  Listed times lack verified
    time zones, so all imported games remain date-only and unverified.
    """

    if not isinstance(raw_text, str):
        raise TypeError("raw_text must be text")
    if league not in {"EPL", "NPFL"}:
        raise ValueError("league must be EPL or NPFL")
    start_year, end_year = _season_years(season)
    if type(rollover_month) is not int or not 1 <= rollover_month <= 12:
        raise ValueError("rollover_month must be an integer from 1 through 12")
    source = _text(source, "source")
    fetched_at = _utc(fetched_at, "fetched_at")
    rows: list[HistoricalMatch] = []
    seen: dict[str, HistoricalMatch] = {}
    current_date: date | None = None
    for line_number, original in enumerate(raw_text.splitlines(), start=1):
        line = original.strip()
        if not line or line.startswith(("#", "=", "▪")):
            continue
        game_date = _football_date(line, start_year, end_year, rollover_month=rollover_month)
        if game_date is not None:
            current_date = game_date
            continue
        line = _TIME_PREFIX.sub("", line)
        if re.search(r"\b\d+-\d+\s+\[awarded\]$", line, re.IGNORECASE):
            continue  # Administrative score, not a played-game training example.
        # A failed "v" result must not fall through to the score-middle
        # grammar: otherwise ``Home v Away 1-0 (awarded)`` can be mistaken for
        # a team called "Home v Away" playing a team called "(awarded)".
        found = _V_SCORE_END.fullmatch(line) if " v " in line else _SCORE_MIDDLE.fullmatch(line)
        if found is None:
            if " v " in line and re.search(r"\b\d+-\d+\b", line):
                raise HistoryParseError(f"OpenFootball line {line_number}: unsupported result format")
            continue  # Round labels, scorer lines, and unplayed fixtures.
        if current_date is None:
            raise HistoryParseError(f"OpenFootball line {line_number}: result has no date header")
        home = _text(found.group("home"), "home_team")
        away = _text(found.group("away"), "away_team")
        available = conservative_result_available_at(current_date)
        if available > fetched_at:
            continue  # A date-only score cannot be proven final yet.
        record = HistoricalMatch(
            match_id=_football_id(league, season, current_date, home, away),
            sport="football",
            league=league,
            season=season,
            home_team=home,
            away_team=away,
            match_date=current_date,
            kickoff_at=None,
            kickoff_unverified=True,
            home_score=int(found.group("home_score")),
            away_score=int(found.group("away_score")),
            result_available_at=available,
            source=source,
            fetched_at=fetched_at,
        )
        previous = seen.get(record.match_id)
        if previous is not None:
            if (previous.home_score, previous.away_score) != (record.home_score, record.away_score):
                raise HistoryParseError(f"OpenFootball line {line_number}: conflicting duplicate game")
            continue
        seen[record.match_id] = record
        rows.append(record)
    if not rows:
        raise HistoryParseError("OpenFootball source has no usable final results")
    return tuple(sorted(rows, key=lambda row: (row.match_date, row.match_id)))


def _csv_bool(value: str, line_number: int) -> bool | None:
    normalized = value.strip().lower()
    if normalized in {"true", "t", "1"}:
        return True
    if normalized in {"false", "f", "0"}:
        return False
    if normalized in {"", "na", "n/a", "null", "none"}:
        return None
    raise HistoryParseError(f"NBA CSV line {line_number}: invalid completed flag")


def parse_nba_schedule_csv(
    raw_text: str, *, season_end_year: int, source: str, fetched_at: datetime
) -> tuple[HistoricalMatch, ...]:
    """Parse verified NBA *regular season* finals from a hoopR schedule CSV.

    SportsDataverse hoopR data are CC BY 4.0.  Displaying imported scores
    requires credit to the source and a link to the license.  ``game_date`` is
    treated as a date only; a naive/unstated-zone ``game_date_time`` is ignored.
    """

    nba_schedule_source_url(season_end_year)
    if not isinstance(raw_text, str):
        raise TypeError("raw_text must be text")
    source = _text(source, "source")
    fetched_at = _utc(fetched_at, "fetched_at")
    reader = csv.DictReader(io.StringIO(raw_text, newline=""))
    if reader.fieldnames is None:
        raise HistoryParseError("NBA CSV has no header")
    reader.fieldnames = [name.strip().lstrip("\ufeff") for name in reader.fieldnames]
    required = {
        "game_id", "season", "season_type", "type_id", "type_abbreviation",
        "home_display_name", "away_display_name", "home_score", "away_score",
        "status_type_completed", "status_type_name", "status_type_state",
    }
    missing = sorted(required - set(reader.fieldnames))
    if not ({"game_date", "date"} & set(reader.fieldnames)):
        missing.append("game_date or date")
    if missing:
        raise HistoryParseError("NBA CSV schema changed; missing: " + ", ".join(missing))
    rows: list[HistoricalMatch] = []
    seen: dict[str, HistoricalMatch] = {}
    for line_number, row in enumerate(reader, start=2):
        if None in row:
            raise HistoryParseError(f"NBA CSV line {line_number}: too many columns")
        row = {key: (value or "").strip() for key, value in row.items()}
        if not any(row.values()):
            continue
        if row["season"] != str(season_end_year):
            raise HistoryParseError(f"NBA CSV line {line_number}: unexpected season")
        if row["season_type"] != "2":
            continue
        if row["type_id"] != "1" or row["type_abbreviation"].upper() != "STD":
            continue  # Exclude All-Star mini-games and cup finals.
        if (
            _csv_bool(row["status_type_completed"], line_number) is not True
            or row["status_type_state"].lower() != "post"
            or not row["status_type_name"].upper().startswith("STATUS_FINAL")
        ):
            continue
        game_id = row["game_id"]
        if not game_id.isdecimal():
            raise HistoryParseError(f"NBA CSV line {line_number}: invalid game_id")
        raw_date = row.get("game_date") or row.get("date") or ""
        try:
            game_date = date.fromisoformat(raw_date)
        except ValueError as error:
            raise HistoryParseError(f"NBA CSV line {line_number}: invalid game date") from error
        try:
            home_score, away_score = int(row["home_score"]), int(row["away_score"])
        except ValueError as error:
            raise HistoryParseError(f"NBA CSV line {line_number}: invalid final scores") from error
        if min(home_score, away_score) < 0 or home_score == away_score:
            raise HistoryParseError(f"NBA CSV line {line_number}: impossible final scores")
        available = conservative_result_available_at(game_date)
        if available > fetched_at:
            continue
        record = HistoricalMatch(
            match_id=f"nba:{season_end_year}:{game_id}",
            sport="basketball",
            league="NBA",
            season=str(season_end_year),
            home_team=row["home_display_name"],
            away_team=row["away_display_name"],
            match_date=game_date,
            kickoff_at=None,
            kickoff_unverified=True,
            home_score=home_score,
            away_score=away_score,
            result_available_at=available,
            source=source,
            fetched_at=fetched_at,
        )
        previous = seen.get(record.match_id)
        if previous is not None:
            if previous != record:
                raise HistoryParseError(f"NBA CSV line {line_number}: conflicting duplicate game_id")
            continue
        seen[record.match_id] = record
        rows.append(record)
    if not rows:
        raise HistoryParseError("NBA CSV has no usable regular-season final results")
    return tuple(sorted(rows, key=lambda row: (row.match_date, row.match_id)))


def _read_local_utf8(path: Path | str) -> str:
    with Path(path).open("rb") as source:
        raw = source.read(_MAX_SOURCE_BYTES + 1)
    if len(raw) > _MAX_SOURCE_BYTES:
        raise HistoryParseError("historical source exceeds 20 MB")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise HistoryParseError("historical source is not UTF-8") from error


def load_openfootball_file(
    path: Path | str, *, league: str, season: str, source: str, fetched_at: datetime,
    rollover_month: int = 7,
) -> tuple[HistoricalMatch, ...]:
    return parse_openfootball_txt(
        _read_local_utf8(path), league=league, season=season, source=source,
        fetched_at=fetched_at, rollover_month=rollover_month,
    )


def load_nba_schedule_file(
    path: Path | str, *, season_end_year: int, source: str, fetched_at: datetime
) -> tuple[HistoricalMatch, ...]:
    return parse_nba_schedule_csv(
        _read_local_utf8(path), season_end_year=season_end_year,
        source=source, fetched_at=fetched_at,
    )
