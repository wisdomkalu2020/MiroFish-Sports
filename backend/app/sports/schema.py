"""Immutable, source-attributed records for pre-match sports dossiers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Protocol


def _utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _required_text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    return value.strip()


@dataclass(frozen=True, slots=True)
class Provenance:
    """When source evidence was available, in UTC.

    ``fetched_at`` is when this system retrieved the record.  An adapter may
    set ``archived_at`` only when it can verify a snapshot captured at that
    time; a current page's nominal publication date is *not* such evidence.
    ``published_at`` and ``observed_at`` are additional upper bounds on when
    the *exact fact* could have been known.
    """

    source: str
    fetched_at: datetime
    published_at: datetime | None = None
    observed_at: datetime | None = None
    archived_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", _required_text(self.source, "source"))
        for field in ("fetched_at", "published_at", "observed_at", "archived_at"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, _utc(value, field))
        if self.archived_at is not None and self.archived_at > self.fetched_at:
            raise ValueError("archived_at cannot be later than fetched_at")

    def known_by(self, cutoff_at: datetime) -> bool:
        """Reject retrospectively fetched facts without an old captured copy."""

        cutoff_at = _utc(cutoff_at, "cutoff_at")
        capture_at = self.archived_at if self.archived_at is not None else self.fetched_at
        return (
            capture_at <= cutoff_at
            and (self.published_at is None or self.published_at <= capture_at)
            and (self.observed_at is None or self.observed_at <= capture_at)
        )


class FactCategory(str, Enum):
    TEAM = "team"
    COACH = "coach"
    KEY_PLAYER = "key_player"
    VENUE = "venue"
    FORM = "form"
    HEAD_TO_HEAD = "head_to_head"
    INJURY_LINEUP = "injury_lineup"
    REST_DAYS = "rest_days"
    SEASON_HISTORY = "season_history"
    REFEREE = "referee"


@dataclass(frozen=True, slots=True)
class MatchFixture:
    """The schedule record used to calculate an as-of cutoff.

    A corrected kickoff requires a new record with new provenance.  A
    historical dossier cannot use a correction first captured after cutoff.
    """

    match_id: str
    sport: str
    competition: str
    season: str
    kickoff_at: datetime
    home_team: str
    away_team: str
    venue: str | None
    provenance: Provenance
    kickoff_unverified: bool = False
    kickoff_stale: bool = False
    data_warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field in ("match_id", "sport", "competition", "season", "home_team", "away_team"):
            object.__setattr__(self, field, _required_text(getattr(self, field), field))
        object.__setattr__(self, "sport", self.sport.lower())
        object.__setattr__(self, "kickoff_at", _utc(self.kickoff_at, "kickoff_at"))
        if self.home_team == self.away_team:
            raise ValueError("home_team and away_team must differ")
        if self.venue is not None:
            object.__setattr__(self, "venue", _required_text(self.venue, "venue"))
        if not isinstance(self.provenance, Provenance):
            raise TypeError("provenance must be a Provenance record")
        if not isinstance(self.kickoff_unverified, bool) or not isinstance(self.kickoff_stale, bool):
            raise TypeError("kickoff flags must be booleans")
        if isinstance(self.data_warnings, str):
            raise TypeError("data_warnings must be a sequence of warning messages")
        object.__setattr__(
            self,
            "data_warnings",
            tuple(_required_text(warning, "data warning") for warning in self.data_warnings),
        )


@dataclass(frozen=True, slots=True)
class SportsFact:
    category: FactCategory
    subject: str
    statement: str
    provenance: Provenance

    def __post_init__(self) -> None:
        if not isinstance(self.category, FactCategory):
            raise TypeError("category must be a FactCategory")
        object.__setattr__(self, "subject", _required_text(self.subject, "subject"))
        object.__setattr__(self, "statement", _required_text(self.statement, "statement"))
        if not isinstance(self.provenance, Provenance):
            raise TypeError("provenance must be a Provenance record")


@dataclass(frozen=True, slots=True)
class MatchData:
    fixture: MatchFixture
    facts: tuple[SportsFact, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.fixture, MatchFixture):
            raise TypeError("fixture must be a MatchFixture")
        object.__setattr__(self, "facts", tuple(self.facts))
        if any(not isinstance(fact, SportsFact) for fact in self.facts):
            raise TypeError("facts must contain SportsFact records")


class SportAdapter(Protocol):
    """Source-specific loader contract; football and basketball use this shape.

    ``cutoff_at`` lets adapters avoid requesting post-cutoff statistics.  The
    dossier builder independently filters the returned records, so a loader
    cannot bypass the as-of rule by accident.
    """

    sport: str

    def load_match(self, match_id: str, *, cutoff_at: datetime) -> MatchData: ...
