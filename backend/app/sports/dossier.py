"""Build graph-ready pre-match text without allowing future source evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isfinite
from typing import Iterable

from .schema import FactCategory, MatchFixture, SportsFact


class CutoffViolation(ValueError):
    """The schedule itself was not known at the requested pre-match cutoff."""


_SECTIONS = (
    (FactCategory.TEAM, "Teams"),
    (FactCategory.COACH, "Coaches"),
    (FactCategory.KEY_PLAYER, "Key players"),
    (FactCategory.VENUE, "Venue context"),
    (FactCategory.FORM, "Recent form"),
    (FactCategory.HEAD_TO_HEAD, "Head-to-head"),
    (FactCategory.INJURY_LINEUP, "Injuries and lineups"),
    (FactCategory.REST_DAYS, "Rest days"),
    (FactCategory.SEASON_HISTORY, "Season history"),
    (FactCategory.REFEREE, "Referee"),
)


def _inline(value: str) -> str:
    """Keep untrusted source strings on one line in the seed document."""

    return " ".join(value.split()).replace("|", "\\|")


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class MatchDossier:
    fixture: MatchFixture
    cutoff_at: datetime
    cutoff_hours: float
    included_facts: tuple[SportsFact, ...]
    excluded_fact_count: int

    def __post_init__(self) -> None:
        """Preserve the cutoff invariant even if constructed without the builder."""

        object.__setattr__(self, "included_facts", tuple(self.included_facts))
        if self.cutoff_at != self.fixture.kickoff_at - timedelta(hours=self.cutoff_hours):
            raise CutoffViolation("Dossier cutoff does not match the fixture")
        if not self.fixture.provenance.known_by(self.cutoff_at):
            raise CutoffViolation("Fixture was not known by the dossier cutoff")
        if any(not fact.provenance.known_by(self.cutoff_at) for fact in self.included_facts):
            raise CutoffViolation("Dossier includes a fact not known by the cutoff")

    def to_markdown(self) -> str:
        """Plain Markdown suitable as MiroFish's existing graph seed file."""

        f = self.fixture
        lines = [
            f"# Pre-match dossier: {_inline(f.home_team)} vs {_inline(f.away_team)}",
            "",
            f"- Match ID: {_inline(f.match_id)}",
            f"- Sport: {_inline(f.sport)}",
            f"- Competition: {_inline(f.competition)}; season: {_inline(f.season)}",
            f"- Scheduled kickoff (UTC): {_timestamp(f.kickoff_at)}",
            f"- Evidence cutoff (UTC): {_timestamp(self.cutoff_at)} "
            f"({self.cutoff_hours:g} hours before scheduled kickoff)",
            f"- Fixture source: {_inline(f.provenance.source)} "
            f"(fetched {_timestamp(f.provenance.fetched_at)})",
            f"- Venue: {_inline(f.venue) if f.venue else 'Unknown'}",
        ]
        if f.kickoff_unverified or f.kickoff_stale:
            warnings = []
            if f.kickoff_unverified:
                warnings.append("unverified")
            if f.kickoff_stale:
                warnings.append("stale")
            lines.append(f"- Kickoff warning: {' and '.join(warnings)} schedule; verify before use")
        for warning in f.data_warnings:
            lines.append(f"- Data warning: {_inline(warning)}")
        if self.excluded_fact_count:
            lines.append(
                f"- Data-freshness warning: {self.excluded_fact_count} fact(s) omitted "
                "because pre-cutoff availability was not proven"
            )
        lines.append("")
        for category, heading in _SECTIONS:
            lines.extend((f"## {heading}", ""))
            facts = [fact for fact in self.included_facts if fact.category == category]
            if not facts:
                lines.append("Unknown: no source fact proven available by the cutoff.")
            else:
                for fact in facts:
                    source = fact.provenance
                    captured_at = source.archived_at or source.fetched_at
                    lines.append(
                        f"- {_inline(fact.subject)}: {_inline(fact.statement)} "
                        f"[source: {_inline(source.source)}; captured: {_timestamp(captured_at)}]"
                    )
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"


def build_match_dossier(
    fixture: MatchFixture,
    facts: Iterable[SportsFact],
    *,
    cutoff_hours: float,
) -> MatchDossier:
    """Select facts known by kickoff minus ``cutoff_hours``.

    This deliberately excludes a record fetched after the cutoff even when
    it claims an old publication/observation date, unless the adapter also
    verified a pre-cutoff archived capture.  Historical pages can change.
    """

    if not isinstance(fixture, MatchFixture):
        raise TypeError("fixture must be a MatchFixture")
    if isinstance(cutoff_hours, bool) or not isinstance(cutoff_hours, (float, int)):
        raise ValueError("cutoff_hours must be an explicit non-negative number")
    if not isfinite(cutoff_hours) or cutoff_hours < 0:
        raise ValueError("cutoff_hours must be finite and non-negative")
    try:
        cutoff_at = fixture.kickoff_at - timedelta(hours=cutoff_hours)
    except OverflowError as error:
        raise ValueError("cutoff_hours is too large") from error
    if not fixture.provenance.known_by(cutoff_at):
        raise CutoffViolation(
            "Fixture kickoff and participants were not proven available by the cutoff"
        )

    included: list[SportsFact] = []
    excluded_count = 0
    for fact in facts:
        if not isinstance(fact, SportsFact):
            raise TypeError("facts must contain SportsFact records")
        if fact.provenance.known_by(cutoff_at):
            included.append(fact)
        else:
            excluded_count += 1
    return MatchDossier(
        fixture=fixture,
        cutoff_at=cutoff_at,
        cutoff_hours=float(cutoff_hours),
        included_facts=tuple(included),
        excluded_fact_count=excluded_count,
    )
