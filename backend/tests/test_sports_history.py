"""Historical-score imports must keep date-only results out of live features."""

from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone

import pytest

from app.sports.history import (
    HistoricalMatch,
    HistoryParseError,
    conservative_result_available_at,
    load_nba_schedule_file,
    load_openfootball_file,
    nba_schedule_source_url,
    openfootball_source_url,
    parse_nba_schedule_csv,
    parse_openfootball_txt,
    results_known_by,
)


UTC = timezone.utc
FETCHED = datetime(2026, 10, 14, 12, tzinfo=UTC)
EPL_URL = openfootball_source_url("EPL", "2025-26")
NPFL_URL = openfootball_source_url("NPFL", "2025-26")
NBA_URL = nba_schedule_source_url(2026)


def football(raw, *, league="EPL", season="2025-26", fetched_at=FETCHED):
    return parse_openfootball_txt(
        raw, league=league, season=season,
        source=openfootball_source_url(league, season), fetched_at=fetched_at,
    )


def test_epl_parses_both_source_score_orders_and_skips_unplayed():
    rows = football(
        """= England | Premier League 2025/26
▪ Regular Season - 1
Fri Aug 15 2025
  19:00 Liverpool 4-2 (1-0) Bournemouth
            (goal scorer line)
Sat Aug 16
  12:30 Aston Villa 0-0 (0-0) Newcastle United
  15:00 Arsenal v Fulham 3-1 (1-1)
Sun Aug 17
  Chelsea v Everton
"""
    )
    assert {(r.home_team, r.away_team, r.home_score, r.away_score) for r in rows} == {
        ("Liverpool", "Bournemouth", 4, 2),
        ("Arsenal", "Fulham", 3, 1),
        ("Aston Villa", "Newcastle United", 0, 0),
    }
    assert all(row.kickoff_at is None and row.kickoff_unverified for row in rows)
    assert rows[0].source == EPL_URL
    assert rows[0].fetched_at == FETCHED
    assert rows[0].result_available_at == datetime(2025, 8, 17, tzinfo=UTC)


def test_npfl_explicit_and_numeric_dates_with_out_of_order_rescheduled_games():
    recent = football(
        """= Nigeria Professional League 2025/2026
▪ Matchday 5
Sun Sep 21 2025
  Remo Stars FC v Rivers United FC 1-1
Wed Nov 19 2025
  Enyimba FC v Bendel FC 2-0
▪ Matchday 6
Sat Sep 27 2025
  Barau FC v Warri Wolves FC 0-1
""",
        league="NPFL",
    )
    assert [r.match_date for r in recent] == [date(2025, 9, 21), date(2025, 9, 27), date(2025, 11, 19)]
    assert recent[0].league == "NPFL"
    older = football(
        """▪ Matchday 1
08.09.
16:00 Abia Warriors FC v Remo Stars FC 0-2 (0-0)
02.01.
16:00 Remo Stars FC v Kano Pillars FC 2-0 (1-0)
""",
        league="NPFL", season="2024-25",
    )
    assert [r.match_date for r in older] == [date(2024, 9, 8), date(2025, 1, 2)]


def test_date_only_results_are_not_available_same_day_or_next_day():
    fetched = datetime(2026, 10, 14, 12, tzinfo=UTC)
    rows = football(
        """Mon Oct 12 2026
Home A 2-1 Away A
Tue Oct 13 2026
Home B 3-0 Away B
""",
        season="2026-27", fetched_at=fetched,
    )
    assert len(rows) == 1  # Yesterday's listed final is still held back.
    assert rows[0].result_available_at == datetime(2026, 10, 14, tzinfo=UTC)
    assert results_known_by(rows, datetime(2026, 10, 13, 23, tzinfo=UTC)) == ()
    assert results_known_by(rows, datetime(2026, 10, 14, tzinfo=UTC)) == rows


def test_openfootball_rejects_undated_and_conflicting_scores():
    with pytest.raises(HistoryParseError, match="no date header"):
        football("Home A v Away A 1-0")
    with pytest.raises(HistoryParseError, match="conflicting duplicate"):
        football("Fri Aug 15 2025\nHome A v Away A 1-0\nHome A v Away A 2-0")
    with pytest.raises(HistoryParseError, match="unsupported result format"):
        football("Fri Aug 15 2025\nHome A v Away A 1-0 (unverified)")


def test_awarded_score_is_excluded_as_non_played_result():
    rows = football(
        "Fri Aug 15 2025\nHome A v Away A 3-0 [awarded]\nHome B v Away B 2-1"
    )
    assert len(rows) == 1
    assert rows[0].home_team == "Home B"


NBA_HEADER = (
    "game_id,season,season_type,type_id,type_abbreviation,home_display_name,"
    "away_display_name,home_score,away_score,status_type_completed,status_type_name,"
    "status_type_state,game_date,game_date_time\n"
)


def nba_row(game_id, *, day="2025-10-22", season_type="2", type_id="1",
            abbreviation="STD", completed="TRUE", status="STATUS_FINAL",
            state="post", home_score="120", away_score="118", home="Boston Celtics",
            away="New York Knicks"):
    return (
        f"{game_id},2026,{season_type},{type_id},{abbreviation},{home},{away},"
        f"{home_score},{away_score},{completed},{status},{state},{day},"
        "2025-10-22 19:30:00\n"
    )


def test_nba_imports_only_verified_standard_regular_season_finals():
    text = NBA_HEADER + "".join([
        nba_row("401001"),
        nba_row("401002", season_type="3"),
        nba_row("401003", type_id="99", abbreviation="ASG"),
        nba_row("401004", completed="FALSE", status="STATUS_SCHEDULED", state="pre"),
    ])
    rows = parse_nba_schedule_csv(
        text, season_end_year=2026, source=NBA_URL, fetched_at=FETCHED
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.match_id == "nba:2026:401001"
    assert row.sport == "basketball"
    assert row.league == "NBA"
    assert row.match_date == date(2025, 10, 22)
    assert row.kickoff_at is None and row.kickoff_unverified
    assert row.result_available_at == datetime(2025, 10, 24, tzinfo=UTC)
    assert row.home_score == 120 and row.away_score == 118


def test_nba_validation_rejects_schema_and_conflicting_game_id():
    with pytest.raises(HistoryParseError, match="missing"):
        parse_nba_schedule_csv("game_id,season\n1,2026\n", season_end_year=2026,
                               source=NBA_URL, fetched_at=FETCHED)
    with pytest.raises(HistoryParseError, match="conflicting duplicate"):
        parse_nba_schedule_csv(
            NBA_HEADER + nba_row("401001") + nba_row("401001", home_score="121"),
            season_end_year=2026, source=NBA_URL, fetched_at=FETCHED,
        )
    with pytest.raises(HistoryParseError, match="impossible final"):
        parse_nba_schedule_csv(
            NBA_HEADER + nba_row("401001", home_score="118"),
            season_end_year=2026, source=NBA_URL, fetched_at=FETCHED,
        )


def test_nba_date_only_final_is_held_back_until_after_next_day():
    rows = parse_nba_schedule_csv(
        NBA_HEADER + nba_row("401001", day="2026-10-12")
        + nba_row("401002", day="2026-10-13"),
        season_end_year=2026, source=NBA_URL, fetched_at=FETCHED,
    )
    assert [r.match_id for r in rows] == ["nba:2026:401001"]
    assert results_known_by(rows, datetime(2026, 10, 13, 23, tzinfo=UTC)) == ()


def test_local_file_loaders_are_explicit_and_no_network(tmp_path):
    football_file = tmp_path / "npfl.txt"
    football_file.write_text("Sun Aug 24 2025\nRemo Stars FC v Rivers United FC 1-1\n", encoding="utf-8")
    nba_file = tmp_path / "nba.csv"
    nba_file.write_text(NBA_HEADER + nba_row("401001"), encoding="utf-8")
    assert len(load_openfootball_file(
        football_file, league="NPFL", season="2025-26", source=NPFL_URL,
        fetched_at=FETCHED,
    )) == 1
    assert len(load_nba_schedule_file(
        nba_file, season_end_year=2026, source=NBA_URL, fetched_at=FETCHED,
    )) == 1


def test_historical_record_is_immutable_and_cannot_claim_early_date_only_availability():
    row = football("Fri Aug 15 2025\nHome A v Away A 1-0")[0]
    with pytest.raises(FrozenInstanceError):
        row.home_score = 99
    with pytest.raises(ValueError, match="too early"):
        replace(row, result_available_at=datetime(2025, 8, 16, tzinfo=UTC))
    with pytest.raises(ValueError, match="timezone-aware"):
        replace(row, fetched_at=datetime(2026, 1, 1))
    with pytest.raises(ValueError, match="cannot be fetched"):
        replace(row, fetched_at=datetime(2025, 8, 16, tzinfo=UTC))
    assert conservative_result_available_at(date(2025, 8, 15)) == datetime(2025, 8, 17, tzinfo=UTC)
