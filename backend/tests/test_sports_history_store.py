"""Imported scores remain auditable, immutable, and conflict-free."""

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from app.sports.history import parse_openfootball_txt, results_known_by
from app.sports.history_store import (
    HistoryStoreError, default_history_root, load_history, save_history,
)
from scripts.import_sports_history import main as import_main


UTC = timezone.utc
SOURCE = "https://raw.githubusercontent.com/openfootball/england/master/2025-26/1-premierleague.txt"
FETCHED = datetime(2026, 10, 14, 12, tzinfo=UTC)
RAW = "Fri Aug 15 2025\nLiverpool v Bournemouth 4-2\nSat Aug 16 2025\nArsenal v Fulham 3-1\n"


def _matches():
    return parse_openfootball_txt(
        RAW, league="EPL", season="2025-26", source=SOURCE, fetched_at=FETCHED,
    )


def test_round_trip_idempotence_and_date_only_availability(tmp_path):
    root = tmp_path / "history"
    rows = _matches()
    snapshot = save_history(root, rows)
    assert snapshot.name.startswith("history-") and snapshot.suffix == ".jsonl"
    before = snapshot.read_bytes()
    assert save_history(root, reversed(rows)) == snapshot
    assert snapshot.read_bytes() == before
    assert len(list(root.glob("history-*.jsonl"))) == 1
    loaded = load_history(root, sport="football", league="EPL")
    assert loaded == rows
    assert results_known_by(loaded, datetime(2025, 8, 16, 23, tzinfo=UTC)) == ()
    assert results_known_by(loaded, datetime(2025, 8, 17, tzinfo=UTC)) == rows[:1]
    assert load_history(root, sport="basketball", league="NBA") == ()


def test_same_final_new_provenance_deduplicates_to_oldest_fetch(tmp_path):
    root = tmp_path / "history"
    rows = _matches()
    first = save_history(root, rows)
    later = [replace(row, source="https://example.org/mirror.txt",
                     fetched_at=datetime(2026, 10, 15, tzinfo=UTC)) for row in rows]
    second = save_history(root, later)
    assert first != second
    assert len(list(root.glob("history-*.jsonl"))) == 2
    assert load_history(root, sport="football", league="EPL") == rows


def test_conflicting_final_is_rejected_without_changing_store(tmp_path):
    root = tmp_path / "history"
    rows = _matches()
    original = save_history(root, rows)
    with pytest.raises(HistoryStoreError, match="conflicting final"):
        save_history(root, [replace(rows[0], home_score=5)])
    assert list(root.glob("history-*.jsonl")) == [original]
    assert load_history(root, sport="football", league="EPL") == rows


def test_store_rejects_invalid_provenance_and_tampering(tmp_path):
    root = tmp_path / "history"
    rows = _matches()
    with pytest.raises(HistoryStoreError, match="HTTP"):
        save_history(root, [replace(rows[0], source="local file")])
    assert not root.exists()
    snapshot = save_history(root, rows)
    original = snapshot.read_bytes()
    tampered = original.replace(b'"home_score":4', b'"home_score":5')
    assert tampered != original
    snapshot.write_bytes(tampered)
    with pytest.raises(HistoryStoreError, match="checksum mismatch"):
        load_history(root, sport="football", league="EPL")


def test_cli_imports_explicit_local_file_without_fetching(tmp_path, capsys, monkeypatch):
    source_file = tmp_path / "premierleague.txt"
    source_file.write_text(RAW, encoding="utf-8")
    root = tmp_path / "history"
    monkeypatch.setenv("MIROFISH_SPORTS_HISTORY_DIR", str(root))
    assert default_history_root() == root
    assert import_main([
        "--football-file", "EPL", "2025-26", str(source_file), SOURCE,
        "2026-10-14T12:00:00Z",
    ]) == 0
    assert '"records": 2' in capsys.readouterr().out
    assert load_history(root, sport="football", league="EPL") == _matches()


def test_cli_imports_nba_regular_season_final(tmp_path, capsys):
    source_file = tmp_path / "nba_schedule_2026.csv"
    source_file.write_text(
        "game_id,season,season_type,type_id,type_abbreviation,home_display_name,"
        "away_display_name,home_score,away_score,status_type_completed,"
        "status_type_name,status_type_state,game_date\n"
        "401001,2026,2,1,STD,Boston Celtics,New York Knicks,120,118,TRUE,"
        "STATUS_FINAL,post,2025-10-22\n",
        encoding="utf-8",
    )
    root = tmp_path / "history"
    assert import_main([
        "--nba-file", "2026", str(source_file),
        "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/"
        "espn_nba_schedules/nba_schedule_2026.csv",
        "2026-10-14T12:00:00Z", "--history-dir", str(root),
    ]) == 0
    assert '"league": "NBA"' in capsys.readouterr().out
    rows = load_history(root, sport="basketball", league="NBA")
    assert len(rows) == 1
    assert rows[0].home_score == 120 and rows[0].away_score == 118
    assert rows[0].kickoff_at is None and rows[0].kickoff_unverified


def test_cli_rejects_unzoned_fetch_time(tmp_path):
    source_file = tmp_path / "premierleague.txt"
    source_file.write_text(RAW, encoding="utf-8")
    with pytest.raises(SystemExit) as error:
        import_main([
            "--football-file", "EPL", "2025-26", str(source_file), SOURCE,
            "2026-10-14T12:00:00",
            "--history-dir", str(tmp_path / "history"),
        ])
    assert error.value.code == 2
    assert not (tmp_path / "history").exists()
