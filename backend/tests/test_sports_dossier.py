"""The sports graph seed must be reproducibly pre-match."""

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import pytest

from app.sports import (
    CutoffViolation,
    FactCategory,
    MatchData,
    MatchDossier,
    MatchFixture,
    Provenance,
    SportsFact,
    build_match_dossier,
)


KICKOFF = datetime(2026, 6, 14, 18, 0, tzinfo=timezone.utc)
CUTOFF = KICKOFF - timedelta(hours=6)


def source(fetched_at, *, published_at=None, observed_at=None, archived_at=None):
    return Provenance(
        source="https://example.test/sports/source",
        fetched_at=fetched_at,
        published_at=published_at,
        observed_at=observed_at,
        archived_at=archived_at,
    )


def fixture(**changes):
    values = dict(
        match_id="match-1",
        sport="football",
        competition="Example League",
        season="2025/26",
        kickoff_at=KICKOFF,
        home_team="Home United",
        away_team="Away City",
        venue="Example Stadium",
        provenance=source(CUTOFF - timedelta(hours=1)),
    )
    values.update(changes)
    return MatchFixture(**values)


def fact(category, statement, *, provenance=None):
    return SportsFact(
        category=category,
        subject="Home United",
        statement=statement,
        provenance=provenance or source(CUTOFF - timedelta(minutes=1)),
    )


def test_dossier_includes_only_pre_cutoff_live_capture():
    known = fact(FactCategory.COACH, "Coach A was appointed last month")
    leaked = fact(
        FactCategory.FORM,
        "LEAKED_POST_MATCH_SCORE_4_1",
        provenance=source(CUTOFF + timedelta(minutes=1)),
    )
    dossier = build_match_dossier(fixture(), [known, leaked], cutoff_hours=6)
    text = dossier.to_markdown()

    assert dossier.cutoff_at == CUTOFF
    assert dossier.included_facts == (known,)
    assert dossier.excluded_fact_count == 1
    assert "Coach A was appointed last month" in text
    assert "LEAKED_POST_MATCH_SCORE_4_1" not in text
    assert "1 fact(s) omitted" in text


def test_past_publication_date_on_current_page_is_not_historical_evidence():
    retrospective = fact(
        FactCategory.INJURY_LINEUP,
        "LEAKED_FINAL_LINEUP",
        provenance=source(
            KICKOFF + timedelta(days=1),
            published_at=CUTOFF - timedelta(days=1),
            observed_at=CUTOFF - timedelta(hours=2),
        ),
    )
    text = build_match_dossier(fixture(), [retrospective], cutoff_hours=6).to_markdown()

    assert "LEAKED_FINAL_LINEUP" not in text
    assert "Unknown: no source fact proven available by the cutoff." in text


def test_verified_pre_cutoff_archive_can_be_fetched_later():
    archived = fact(
        FactCategory.FORM,
        "Three wins in five completed matches",
        provenance=source(
            KICKOFF + timedelta(days=1),
            published_at=CUTOFF - timedelta(hours=3),
            observed_at=CUTOFF - timedelta(hours=4),
            archived_at=CUTOFF - timedelta(hours=1),
        ),
    )
    dossier = build_match_dossier(fixture(), [archived], cutoff_hours=6)

    assert dossier.included_facts == (archived,)
    assert "Three wins in five completed matches" in dossier.to_markdown()
    assert f"captured: {(CUTOFF - timedelta(hours=1)).isoformat(timespec='seconds')}" in dossier.to_markdown()


@pytest.mark.parametrize("late_field", ["archived_at", "published_at", "observed_at"])
def test_any_late_evidence_excludes_fact(late_field):
    values = dict(
        fetched_at=KICKOFF + timedelta(days=1),
        archived_at=CUTOFF - timedelta(minutes=1),
        published_at=CUTOFF - timedelta(hours=1),
        observed_at=CUTOFF - timedelta(hours=1),
    )
    values[late_field] = CUTOFF + timedelta(seconds=1)
    late = fact(FactCategory.REST_DAYS, "LEAKED_REST_DAYS", provenance=source(**values))

    dossier = build_match_dossier(fixture(), [late], cutoff_hours=6)
    assert not dossier.included_facts
    assert "LEAKED_REST_DAYS" not in dossier.to_markdown()


def test_source_publication_must_precede_archival_capture_even_if_before_cutoff():
    impossible = fact(
        FactCategory.SEASON_HISTORY,
        "LEAKED_SEASON_FACT",
        provenance=source(
            KICKOFF + timedelta(days=1),
            archived_at=CUTOFF - timedelta(hours=2),
            published_at=CUTOFF - timedelta(hours=1),
        ),
    )
    assert not build_match_dossier(fixture(), [impossible], cutoff_hours=6).included_facts


def test_fixture_itself_must_have_been_captured_by_cutoff():
    corrected_late = fixture(
        provenance=source(KICKOFF + timedelta(hours=1)),
        kickoff_unverified=True,
    )
    with pytest.raises(CutoffViolation, match="Fixture kickoff"):
        build_match_dossier(corrected_late, [], cutoff_hours=6)


def test_dossier_never_invents_missing_sections_and_marks_kickoff_uncertainty():
    basketball = fixture(
        sport="basketball", venue=None, kickoff_unverified=True, kickoff_stale=True
    )
    text = build_match_dossier(basketball, [], cutoff_hours=6).to_markdown()

    for section in (
        "Teams", "Coaches", "Key players", "Venue context", "Recent form",
        "Head-to-head", "Injuries and lineups", "Rest days", "Season history",
    ):
        assert f"## {section}" in text
    assert "Venue: Unknown" in text
    assert "Kickoff warning: unverified and stale schedule" in text
    assert text.count("Unknown: no source fact proven available by the cutoff.") == 10


def test_cutoff_is_required_and_finite():
    with pytest.raises(TypeError):
        build_match_dossier(fixture(), [])
    for hours in (float("nan"), float("inf"), -1, True, "6"):
        with pytest.raises(ValueError):
            build_match_dossier(fixture(), [], cutoff_hours=hours)


def test_records_are_frozen_and_dates_are_utc():
    record = source(CUTOFF)
    with pytest.raises(FrozenInstanceError):
        record.source = "later source"
    with pytest.raises(ValueError, match="timezone-aware"):
        source(datetime(2026, 6, 14, 11))
    with pytest.raises(ValueError, match="archived_at"):
        source(CUTOFF, archived_at=CUTOFF + timedelta(minutes=1))


def test_direct_dossier_construction_cannot_bypass_cutoff():
    leaked = fact(
        FactCategory.HEAD_TO_HEAD,
        "LEAKED_RESULT",
        provenance=source(KICKOFF + timedelta(days=1)),
    )
    with pytest.raises(CutoffViolation, match="fact not known"):
        MatchDossier(fixture(), CUTOFF, 6, (leaked,), 0)
    with pytest.raises(CutoffViolation, match="cutoff does not match"):
        MatchDossier(fixture(), KICKOFF, 6, (), 0)


def test_match_data_copies_fact_sequence_to_an_immutable_tuple():
    facts = [fact(FactCategory.TEAM, "One fact")]
    data = MatchData(fixture=fixture(), facts=facts)
    facts.clear()

    assert len(data.facts) == 1
    assert isinstance(data.facts, tuple)
