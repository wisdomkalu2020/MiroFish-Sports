"""Odds imports and the paper ledger never imply real wagers or backdate picks."""

import json
import sqlite3
from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

from app.sports.history import HistoricalMatch
from app.sports.odds import OddsError, fetch_the_odds_api, manual_quote
from app.sports.paper_ledger import PaperLedger, PaperLedgerError


UTC = timezone.utc


class Clock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


def at(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC)


def quote(clock, *, label="current", kickoff_at=None, odds=None):
    return manual_quote(
        match_id="fixture-42", sport="football", league="EPL",
        home_team="Arsenal", away_team="Chelsea", bookmaker="examplebook",
        decimal_odds=odds or {"home": 2.2, "draw": 3.4, "away": 3.5},
        kickoff_at=kickoff_at or at(16, 20), source="manual screenshot at capture",
        label=label, clock=clock,
    )


def final(*, kickoff_at=None):
    return HistoricalMatch(
        match_id="epl:2026-27:history-id", sport="football", league="EPL",
        season="2026-27", home_team="Arsenal", away_team="Chelsea",
        match_date=date(2026, 10, 16), kickoff_at=kickoff_at,
        kickoff_unverified=kickoff_at is None,
        home_score=2, away_score=1, result_available_at=at(18, 0),
        source="https://example.org/finals.txt", fetched_at=at(20, 8),
    )


def test_manual_quote_devigs_full_market_and_rejects_incomplete():
    clock = Clock(at(16, 10))
    row = quote(clock)
    probabilities = row.devigged()
    assert set(probabilities) == {"home", "draw", "away"}
    assert sum(probabilities.values()) == pytest.approx(1)
    assert row.as_market_odds().recorded_at == at(16, 10)
    with pytest.raises(OddsError, match="full outcome set"):
        quote(clock, odds={"home": 2.2, "away": 3.5})
    with pytest.raises(OddsError, match="before the listed kickoff"):
        quote(Clock(at(16, 21)))


def test_the_odds_api_requires_key_before_transport(monkeypatch):
    monkeypatch.delenv("THE_ODDS_API_KEY", raising=False)
    called = False

    def transport(*args):
        nonlocal called
        called = True
        return b"[]"

    with pytest.raises(OddsError, match="THE_ODDS_API_KEY"):
        fetch_the_odds_api(
            match_id="fixture-42", sport="football", league="EPL",
            home_team="Arsenal", away_team="Chelsea", transport=transport,
        )
    assert not called


def test_the_odds_api_parses_live_h2h_without_leaking_key():
    event = [{
        "id": "provider-event", "sport_key": "soccer_epl",
        "commence_time": "2026-10-16T20:00:00Z",
        "home_team": "Arsenal", "away_team": "Chelsea",
        "bookmakers": [{"key": "examplebook", "last_update": "2026-10-16T09:59:00Z",
                        "markets": [{"key": "h2h", "outcomes": [
                            {"name": "Arsenal", "price": 2.2},
                            {"name": "Draw", "price": 3.4},
                            {"name": "Chelsea", "price": 3.5},
                        ]}]}],
    }]
    calls = []

    def transport(url, params, timeout):
        calls.append((url, params, timeout))
        return json.dumps(event).encode()

    rows = fetch_the_odds_api(
        match_id="fixture-42", sport="football", league="EPL",
        home_team="Arsenal", away_team="Chelsea", api_key="fake-test-secret",
        region="uk", transport=transport, clock=Clock(at(16, 10)),
    )
    assert len(rows) == 1
    assert rows[0].decimal_odds == {"home": 2.2, "draw": 3.4, "away": 3.5}
    assert rows[0].source_kind == "the_odds_api"
    assert "fake-test-secret" not in rows[0].source
    assert calls[0][1]["apiKey"] == "fake-test-secret"
    assert calls[0][1]["markets"] == "h2h"
    assert calls[0][1]["oddsFormat"] == "decimal"


def test_paper_win_clv_and_sqlite_immutability(tmp_path):
    clock = Clock(at(16, 10))
    ledger = PaperLedger(tmp_path / "paper.sqlite", clock=clock)
    opening_id = ledger.record_quote(quote(clock, label="opening"))
    clock.value = at(16, 10, 1)
    prediction_id = ledger.record_prediction(
        quote_id=opening_id, pick="home", stake_ngn=100,
        probabilities={"home": .48, "draw": .28, "away": .24},
        model_version="baseline-test",
    )
    clock.value = at(16, 19)
    ledger.record_quote(quote(
        clock, label="closing", odds={"home": 2.0, "draw": 3.5, "away": 3.8},
    ))
    pending = ledger.prediction_report(prediction_id)
    assert pending["status"] == "pending"
    assert pending["opening_odds"] == 2.2
    assert pending["closing_odds"] == 2.0
    assert pending["closing_line_value_fraction"] == pytest.approx(.1)
    clock.value = at(20, 9)
    ledger.settle_from_final(final(), match_id="fixture-42")
    report = ledger.prediction_report(prediction_id)
    assert report["status"] == "win"
    assert report["net_ngn"] == "120.00"
    assert report["hypothetical"] is True
    assert sum(report["market_implied_probabilities"].values()) == pytest.approx(1)
    assert report["model_minus_market_probability"]["home"] == pytest.approx(
        .48 - report["market_implied_probabilities"]["home"]
    )
    assert ledger.list_predictions(match_id="fixture-42") == (report,)
    with sqlite3.connect(ledger.path) as db:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute("UPDATE events SET payload_json = '{}' WHERE event_id = ?", (prediction_id,))
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute("DELETE FROM events WHERE event_id = ?", (prediction_id,))


def test_corrected_earlier_kickoff_voids_late_prediction_and_closing_quote(tmp_path):
    clock = Clock(at(16, 10))
    ledger = PaperLedger(tmp_path / "paper.sqlite", clock=clock)
    early_quote = ledger.record_quote(quote(clock, label="opening"))
    clock.value = at(16, 10, 1)
    early_prediction = ledger.record_prediction(
        quote_id=early_quote, pick="home", stake_ngn=200,
        probabilities={"home": .5, "draw": .25, "away": .25},
        model_version="baseline-test",
    )
    clock.value = at(16, 19)
    late_quote = ledger.record_quote(quote(clock, label="closing"))
    clock.value = at(16, 19, 10)
    late_prediction = ledger.record_prediction(
        quote_id=late_quote, pick="home", stake_ngn=100,
        probabilities={"home": .5, "draw": .25, "away": .25},
        model_version="baseline-test",
    )
    clock.value = at(16, 19, 30)
    ledger.record_kickoff_correction(
        "fixture-42", kickoff_at=at(16, 18, 30), source="corrected match notice",
    )
    clock.value = at(20, 9)
    ledger.settle_from_final(final(kickoff_at=at(16, 18, 30)), match_id="fixture-42")
    assert ledger.prediction_report(early_prediction)["status"] == "win"
    early = ledger.prediction_report(early_prediction)
    assert early["closing_odds"] is None
    assert early["closing_line_value_fraction"] is None
    late = ledger.prediction_report(late_prediction)
    assert late["status"] == "void_corrected_kickoff"
    assert late["net_ngn"] == "0.00"


def test_prediction_rejects_unknown_price_invalid_stake_and_mismatched_final(tmp_path):
    clock = Clock(at(16, 10))
    ledger = PaperLedger(tmp_path / "paper.sqlite", clock=clock)
    quote_id = ledger.record_quote(quote(clock))
    clock.value = at(16, 10, 1)
    with pytest.raises(PaperLedgerError, match="NGN 100 or 200"):
        ledger.record_prediction(
            quote_id=quote_id, pick="home", stake_ngn=500,
            probabilities={"home": .5, "draw": .25, "away": .25}, model_version="test",
        )
    with pytest.raises(PaperLedgerError, match="sum to one"):
        ledger.record_prediction(
            quote_id=quote_id, pick="home", stake_ngn=100,
            probabilities={"home": .5, "draw": .4, "away": .25}, model_version="test",
        )
    clock.value = at(20, 9)
    with pytest.raises(PaperLedgerError, match="teams do not match"):
        ledger.settle_from_final(replace(final(), away_team="Different Team"), match_id="fixture-42")


def test_nba_manual_quote_and_paper_loss(tmp_path):
    clock = Clock(at(16, 10))
    ledger = PaperLedger(tmp_path / "nba.sqlite", clock=clock)
    nba_quote = manual_quote(
        match_id="nba-fixture-9", sport="basketball", league="NBA",
        home_team="Boston Celtics", away_team="New York Knicks",
        bookmaker="examplebook", decimal_odds={"home": 1.8, "away": 2.1},
        kickoff_at=at(16, 20), source="manual bookmaker screenshot", clock=clock,
    )
    quote_id = ledger.record_quote(nba_quote)
    clock.value = at(16, 10, 1)
    prediction_id = ledger.record_prediction(
        quote_id=quote_id, pick="away", stake_ngn=200,
        probabilities={"home": .55, "away": .45}, model_version="nba-elo-test",
    )
    clock.value = at(20, 9)
    nba_final = HistoricalMatch(
        match_id="nba:2027:9", sport="basketball", league="NBA", season="2027",
        home_team="Boston Celtics", away_team="New York Knicks",
        match_date=date(2026, 10, 16), kickoff_at=None, kickoff_unverified=True,
        home_score=110, away_score=105, result_available_at=at(18, 0),
        source="https://example.org/nba-final.csv", fetched_at=at(20, 8),
    )
    ledger.settle_from_final(nba_final, match_id="nba-fixture-9")
    result = ledger.prediction_report(prediction_id)
    assert result["status"] == "loss"
    assert result["net_ngn"] == "-200.00"
    assert result["closing_line_value_fraction"] is None
