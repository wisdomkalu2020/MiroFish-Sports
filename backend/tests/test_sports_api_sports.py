"""Mocked provider tests: no key or network is needed to check as-of safety."""

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.sports import (
    ApiSportsFootballAdapter,
    ApiSportsNBAAdapter,
    FactCategory,
    SnapshotError,
    SnapshotStore,
    SnapshotUnavailable,
    SportsSourceError,
    build_match_dossier,
)
from app.sports.api_sports import FOOTBALL_BASE, NBA_BASE


KICKOFF = datetime(2026, 6, 14, 18, tzinfo=timezone.utc)
CUTOFF = KICKOFF - timedelta(hours=6)
BEFORE = CUTOFF - timedelta(hours=1)


def body(rows, **extra):
    return json.dumps(
        {"errors": [], "results": len(rows), "paging": {"current": 1, "total": 1},
         "response": rows, **extra}, separators=(",", ":")
    ).encode("utf-8")


def football_game(game_id, when, home_id, away_id, status="FT", goals=(1, 0)):
    return {
        "fixture": {
            "id": game_id, "date": when.isoformat(),
            "timestamp": int(when.timestamp()), "status": {"short": status},
            "venue": {"name": "Example Ground"},
        },
        "league": {"id": 39, "name": "Premier League", "season": 2025},
        "teams": {
            "home": {"id": home_id, "name": "Home United" if home_id == 1 else "Other FC"},
            "away": {"id": away_id, "name": "Away City" if away_id == 2 else "Other FC"},
        },
        "goals": {"home": goals[0], "away": goals[1]},
    }


def target_football():
    record = football_game(77, KICKOFF, 1, 2, status="NS", goals=(None, None))
    record["lineups"] = [{
        "team": {"name": "Home United"},
        "coach": {"name": "Coach Example"},
        "startXI": [{"player": {"name": "Player Example"}}],
    }]
    return record


def nba_game(game_id, when, home_id, away_id, status=3, points=(101, 90)):
    return {
        "id": game_id, "league": "standard", "season": 2025,
        "date": {"start": when.isoformat().replace("+00:00", "Z")},
        "status": {"short": status},
        "arena": {"name": "Example Arena"},
        "teams": {
            "home": {"id": home_id, "name": "Home Hoops" if home_id == 11 else "Other Hoops"},
            "visitors": {"id": away_id, "name": "Away Hoops" if away_id == 22 else "Other Hoops"},
        },
        "scores": {"home": {"points": points[0]}, "visitors": {"points": points[1]}},
    }


class FakeTransport:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, url, headers, timeout):
        self.calls.append((url, dict(headers), timeout))
        if url not in self.responses:
            raise AssertionError(f"Unexpected provider call: {url}")
        return self.responses[url]


def test_snapshot_store_preserves_exact_json_and_asof_selection(tmp_path):
    store = SnapshotStore(tmp_path)
    url = f"{FOOTBALL_BASE}/fixtures?id=77"
    first = b'{ "response": [ {"id": 1} ], "errors": [] }\n'
    second = b'{"response":[{"id":2}],"errors":[]}'
    store.save(url, first, BEFORE)
    store.save(url, second, CUTOFF + timedelta(minutes=1))

    selected = store.latest_as_of(url, CUTOFF)
    assert selected.raw_json == first
    assert selected.source_url == url
    assert selected.fetched_at == BEFORE
    assert store.latest_as_of(url, BEFORE - timedelta(seconds=1)) is None
    assert b"x-apisports-key" not in selected.raw_json
    captures = list(tmp_path.rglob("*.meta.json"))
    assert len(captures) == 2
    assert all(len(str(path)) - len(str(tmp_path)) < 90 for path in captures)
    metadata = json.loads(captures[0].read_text(encoding="utf-8"))
    assert metadata["source_url"] == url
    assert metadata["fetched_at"] in {BEFORE.isoformat(), (CUTOFF + timedelta(minutes=1)).isoformat()}


def test_snapshot_store_reads_original_layout_without_rewriting(tmp_path):
    store = SnapshotStore(tmp_path)
    url = f"{FOOTBALL_BASE}/fixtures?id=77"
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    old_folder = tmp_path / digest[:2] / digest
    old_folder.mkdir(parents=True)
    raw = b'{"response":[{"id":77}],"errors":[]}'
    body_path = old_folder / "old.response.json"
    metadata_path = old_folder / "old.meta.json"
    body_path.write_bytes(raw)
    metadata_path.write_text(json.dumps({
        "source_url": url,
        "fetched_at": BEFORE.isoformat(),
        "body_file": body_path.name,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }), encoding="utf-8")

    selected = store.latest_as_of(url, CUTOFF)
    assert selected is not None
    assert selected.raw_json == raw
    assert selected.fetched_at == BEFORE
    assert body_path.read_bytes() == raw


def test_snapshot_store_detects_changed_response_body(tmp_path):
    store = SnapshotStore(tmp_path)
    url = f"{FOOTBALL_BASE}/fixtures?id=77"
    store.save(url, body([]), BEFORE)
    captured = next(tmp_path.rglob("*.response.json"))
    captured.write_bytes(b"{}")

    with pytest.raises(SnapshotError, match="checksum"):
        store.latest_as_of(url, CUTOFF)


def test_football_epl_uses_only_final_prior_games_and_precutoff_lineup(tmp_path, monkeypatch):
    monkeypatch.setenv("API_FOOTBALL_KEY", "test-football-key")
    fixture_url = f"{FOOTBALL_BASE}/fixtures?id=77"
    lineup_url = f"{FOOTBALL_BASE}/fixtures/lineups?fixture=77"
    season_url = f"{FOOTBALL_BASE}/fixtures?league=39&season=2025"
    prior_home = football_game(70, KICKOFF - timedelta(days=8), 1, 3)
    prior_away = football_game(71, KICKOFF - timedelta(days=7), 3, 2, goals=(0, 2))
    target_after_cutoff = football_game(77, KICKOFF, 1, 2, status="FT", goals=(9, 0))
    future = football_game(78, KICKOFF + timedelta(days=1), 1, 2)
    transport = FakeTransport({
        fixture_url: body([target_football()]),
        lineup_url: body([{
            "team": {"name": "Home United"},
            "coach": {"name": "Coach Example"},
            "startXI": [{"player": {"name": "Player Example"}}],
        }]),
        season_url: body([prior_home, prior_away, target_after_cutoff, future]),
    })
    adapter = ApiSportsFootballAdapter(
        "epl", SnapshotStore(tmp_path), include_lineups=True,
        transport=transport, clock=lambda: BEFORE
    )
    data = adapter.load_match("77", cutoff_at=CUTOFF)
    dossier = build_match_dossier(data.fixture, data.facts, cutoff_hours=6)
    text = dossier.to_markdown()

    assert data.fixture.sport == "football"
    assert data.fixture.venue == "Example Ground"
    assert not data.fixture.kickoff_unverified
    assert "Coach Example" in text
    assert "Player Example" in text
    assert "Home United: Last 1 completed league games before cutoff: W" in text
    assert "Away City: Last 1 completed league games before cutoff: W" in text
    assert "9 wins" not in text
    assert "This season before cutoff: 2 completed meetings" not in text
    assert len(transport.calls) == 3
    assert all(call[1]["x-apisports-key"] == "test-football-key" for call in transport.calls)


def test_historical_cutoff_uses_snapshot_without_network_or_key(tmp_path, monkeypatch):
    monkeypatch.delenv("API_FOOTBALL_KEY", raising=False)
    monkeypatch.delenv("API_SPORTS_KEY", raising=False)
    store = SnapshotStore(tmp_path)
    store.save(f"{FOOTBALL_BASE}/fixtures?id=77", body([target_football()]), BEFORE)
    transport = FakeTransport({})
    adapter = ApiSportsFootballAdapter(
        "epl", store, transport=transport, clock=lambda: KICKOFF + timedelta(days=4)
    )

    data = adapter.load_match("77", cutoff_at=CUTOFF)
    assert data.fixture.match_id == "77"
    assert transport.calls == []
    assert all(f.category != FactCategory.FORM for f in data.facts)


def test_embedded_lineup_is_not_assumed_and_optional_source_stays_unknown(tmp_path):
    store = SnapshotStore(tmp_path)
    store.save(f"{FOOTBALL_BASE}/fixtures?id=77", body([target_football()]), BEFORE)
    adapter = ApiSportsFootballAdapter(
        "epl", store, include_lineups=True, transport=FakeTransport({}),
        clock=lambda: KICKOFF + timedelta(days=1),
    )

    data = adapter.load_match("77", cutoff_at=CUTOFF)
    assert all(f.category not in {FactCategory.COACH, FactCategory.INJURY_LINEUP} for f in data.facts)
    assert "No complete lineup source was captured by the cutoff" in data.fixture.data_warnings[0]
    assert "Coach Example" not in build_match_dossier(data.fixture, data.facts, cutoff_hours=6).to_markdown()


def test_historical_cutoff_does_not_fetch_a_current_fixture(tmp_path, monkeypatch):
    monkeypatch.setenv("API_FOOTBALL_KEY", "test-key")
    transport = FakeTransport({f"{FOOTBALL_BASE}/fixtures?id=77": body([target_football()])})
    adapter = ApiSportsFootballAdapter(
        "epl", SnapshotStore(tmp_path), transport=transport,
        clock=lambda: KICKOFF + timedelta(days=1),
    )

    with pytest.raises(SnapshotUnavailable, match="historical cutoff"):
        adapter.load_match("77", cutoff_at=CUTOFF)
    assert transport.calls == []


def test_response_finishing_after_cutoff_is_captured_but_never_used(tmp_path, monkeypatch):
    monkeypatch.setenv("API_FOOTBALL_KEY", "test-key")
    times = iter([CUTOFF - timedelta(seconds=1), CUTOFF + timedelta(seconds=1)])
    store = SnapshotStore(tmp_path)
    adapter = ApiSportsFootballAdapter(
        "epl", store,
        transport=FakeTransport({f"{FOOTBALL_BASE}/fixtures?id=77": body([target_football()])}),
        clock=lambda: next(times),
    )

    with pytest.raises(SnapshotUnavailable, match="arrived after"):
        adapter.load_match("77", cutoff_at=CUTOFF)
    assert store.latest_as_of(f"{FOOTBALL_BASE}/fixtures?id=77", CUTOFF) is None


def test_npfl_id_is_discovered_by_country_and_name(tmp_path, monkeypatch):
    monkeypatch.setenv("API_FOOTBALL_KEY", "test-key")
    target = target_football()
    target["league"] = {"id": 999, "name": "NPFL", "season": 2025}
    fixture_url = f"{FOOTBALL_BASE}/fixtures?id=77"
    discover_url = f"{FOOTBALL_BASE}/leagues?country=Nigeria&search=NPFL"
    season_url = f"{FOOTBALL_BASE}/fixtures?league=999&season=2025"
    transport = FakeTransport({
        fixture_url: body([target]),
        discover_url: body([{
            "league": {"id": 999, "name": "NPFL"},
            "country": {"name": "Nigeria"},
            "seasons": [{"year": 2025}],
        }]),
        season_url: body([]),
    })
    adapter = ApiSportsFootballAdapter(
        "npfl", SnapshotStore(tmp_path), transport=transport, clock=lambda: BEFORE
    )

    data = adapter.load_match("77", cutoff_at=CUTOFF)
    assert data.fixture.competition == "NPFL"
    assert discover_url in [url for url, _, _ in transport.calls]


def test_npfl_discovery_fails_instead_of_guessing_an_id(tmp_path, monkeypatch):
    monkeypatch.setenv("API_FOOTBALL_KEY", "test-key")
    target = target_football()
    target["league"] = {"id": 999, "name": "NPFL", "season": 2025}
    transport = FakeTransport({
        f"{FOOTBALL_BASE}/fixtures?id=77": body([target]),
        f"{FOOTBALL_BASE}/leagues?country=Nigeria&search=NPFL": body([]),
    })
    adapter = ApiSportsFootballAdapter(
        "npfl", SnapshotStore(tmp_path), transport=transport, clock=lambda: BEFORE
    )

    with pytest.raises(SportsSourceError, match="discovery"):
        adapter.load_match("77", cutoff_at=CUTOFF)


def test_paginated_optional_season_history_warns_without_blocking_fixture(tmp_path, monkeypatch):
    monkeypatch.setenv("API_FOOTBALL_KEY", "test-key")
    transport = FakeTransport({
        f"{FOOTBALL_BASE}/fixtures?id=77": body([target_football()]),
        f"{FOOTBALL_BASE}/fixtures?league=39&season=2025": body(
            [football_game(70, KICKOFF - timedelta(days=8), 1, 3)],
            paging={"current": 1, "total": 2},
        ),
    })
    adapter = ApiSportsFootballAdapter(
        "epl", SnapshotStore(tmp_path), transport=transport, clock=lambda: BEFORE
    )

    data = adapter.load_match("77", cutoff_at=CUTOFF)
    text = build_match_dossier(data.fixture, data.facts, cutoff_hours=6).to_markdown()
    assert data.fixture.match_id == "77"
    assert all(f.category not in {FactCategory.FORM, FactCategory.REST_DAYS,
                                  FactCategory.HEAD_TO_HEAD, FactCategory.SEASON_HISTORY}
               for f in data.facts)
    assert "Data warning: Season-history source was paginated and incomplete" in text
    assert "Unknown: no source fact proven available by the cutoff." in text


def test_nba_loads_prior_final_games_and_uses_explicit_nba_key(tmp_path, monkeypatch):
    monkeypatch.setenv("API_NBA_KEY", "nba-specific-key")
    monkeypatch.setenv("API_SPORTS_KEY", "shared-other-key")
    target = nba_game(88, KICKOFF, 11, 22, status=1, points=(None, None))
    game_url = f"{NBA_BASE}/games?id=88"
    season_url = f"{NBA_BASE}/games?league=standard&season=2025"
    transport = FakeTransport({
        game_url: body([target]),
        season_url: body([
            nba_game(80, KICKOFF - timedelta(days=3), 11, 44),
            nba_game(81, KICKOFF - timedelta(days=2), 44, 22, points=(90, 100)),
            nba_game(88, KICKOFF, 11, 22, status=3, points=(200, 0)),
        ]),
    })
    adapter = ApiSportsNBAAdapter(
        SnapshotStore(tmp_path), transport=transport, clock=lambda: BEFORE
    )
    data = adapter.load_match("88", cutoff_at=CUTOFF)
    text = build_match_dossier(data.fixture, data.facts, cutoff_hours=6).to_markdown()

    assert data.fixture.sport == "basketball"
    assert data.fixture.venue == "Example Arena"
    assert "Home Hoops: Last 1 completed league games before cutoff: W" in text
    assert "Away Hoops: Last 1 completed league games before cutoff: W" in text
    assert "200" not in text
    assert all(call[1]["x-apisports-key"] == "nba-specific-key" for call in transport.calls)


def test_nba_shared_key_fallback_requires_successful_api_response(tmp_path, monkeypatch):
    monkeypatch.delenv("API_NBA_KEY", raising=False)
    monkeypatch.setenv("API_SPORTS_KEY", "shared-key")
    game_url = f"{NBA_BASE}/games?id=88"
    bad = FakeTransport({game_url: b'{"errors":{"key":"invalid"},"response":[]}'})
    adapter = ApiSportsNBAAdapter(
        SnapshotStore(tmp_path), transport=bad, clock=lambda: BEFORE
    )
    with pytest.raises(SportsSourceError, match="API error"):
        adapter.load_match("88", cutoff_at=CUTOFF)
    assert bad.calls[0][1]["x-apisports-key"] == "shared-key"
    assert list(tmp_path.rglob("*.meta.json")) == []

    valid = FakeTransport({
        game_url: body([nba_game(88, KICKOFF, 11, 22, status=1, points=(None, None))]),
        f"{NBA_BASE}/games?league=standard&season=2025": body([]),
    })
    adapter = ApiSportsNBAAdapter(
        SnapshotStore(tmp_path), transport=valid, clock=lambda: BEFORE
    )
    assert adapter.load_match("88", cutoff_at=CUTOFF).fixture.match_id == "88"
    assert all(b"shared-key" not in path.read_bytes() for path in tmp_path.rglob("*.json"))


def test_paginated_nba_history_warns_and_keeps_fixture(tmp_path, monkeypatch):
    monkeypatch.setenv("API_NBA_KEY", "nba-key")
    target = nba_game(88, KICKOFF, 11, 22, status=1, points=(None, None))
    transport = FakeTransport({
        f"{NBA_BASE}/games?id=88": body([target]),
        f"{NBA_BASE}/games?league=standard&season=2025": body(
            [nba_game(80, KICKOFF - timedelta(days=3), 11, 44)],
            paging={"current": 1, "total": 2},
        ),
    })
    adapter = ApiSportsNBAAdapter(
        SnapshotStore(tmp_path), transport=transport, clock=lambda: BEFORE
    )

    data = adapter.load_match("88", cutoff_at=CUTOFF)
    text = build_match_dossier(data.fixture, data.facts, cutoff_hours=6).to_markdown()
    assert data.fixture.match_id == "88"
    assert "Data warning: Season-history source was paginated and incomplete" in text
    assert all(f.category != FactCategory.FORM for f in data.facts)


def test_unverified_and_stale_kickoff_flags_from_incomplete_snapshot(tmp_path):
    old = CUTOFF - timedelta(days=3)
    target = target_football()
    target["fixture"]["timestamp"] = None
    target["fixture"]["status"] = {"short": "PST"}
    store = SnapshotStore(tmp_path)
    store.save(f"{FOOTBALL_BASE}/fixtures?id=77", body([target]), old)
    adapter = ApiSportsFootballAdapter(
        "epl", store, transport=FakeTransport({}), clock=lambda: CUTOFF + timedelta(days=1)
    )

    data = adapter.load_match("77", cutoff_at=CUTOFF)
    assert data.fixture.kickoff_unverified
    assert data.fixture.kickoff_stale


def test_response_pagination_and_missing_credentials_fail_explicitly(tmp_path, monkeypatch):
    monkeypatch.delenv("API_NBA_KEY", raising=False)
    monkeypatch.delenv("API_SPORTS_KEY", raising=False)
    adapter = ApiSportsNBAAdapter(SnapshotStore(tmp_path), transport=FakeTransport({}), clock=lambda: BEFORE)
    with pytest.raises(SportsSourceError, match="API_NBA_KEY"):
        adapter.load_match("88", cutoff_at=CUTOFF)

    paginated = body([nba_game(88, KICKOFF, 11, 22)], paging={"current": 1, "total": 2})
    store = SnapshotStore(tmp_path)
    store.save(f"{NBA_BASE}/games?id=88", paginated, BEFORE)
    adapter = ApiSportsNBAAdapter(store, transport=FakeTransport({}), clock=lambda: CUTOFF + timedelta(days=1))
    with pytest.raises(SportsSourceError, match="paginated"):
        adapter.load_match("88", cutoff_at=CUTOFF)
