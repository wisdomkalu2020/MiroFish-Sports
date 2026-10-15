"""Append-only hypothetical NGN 100/200 match-winner ledger.

This records observations and paper predictions only. It places no wagers,
holds no funds, and has no customer or payment functionality. Odds and
predictions are timestamped when inserted, and SQLite triggers reject updates
or deletes. Kickoff corrections and final scores are appended as new events;
previous predictions remain intact. A newly verified earlier kickoff can void
a prediction that was actually recorded after play began.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from math import isclose, isfinite
from pathlib import Path
from typing import Callable, Mapping
from uuid import uuid4

from app.config import Config

from .evaluation import devig_decimal_odds
from .history import HistoricalMatch
from .odds import OddsQuote, outcomes_for


class PaperLedgerError(ValueError):
    """A paper-ledger event is invalid or conflicts with an existing event."""


def default_paper_ledger_path() -> Path:
    """Return the local hypothetical-ledger file, without creating it."""

    configured = os.environ.get("MIROFISH_PAPER_LEDGER_PATH", "").strip()
    return Path(configured) if configured else Path(Config.UPLOAD_FOLDER) / "sports_paper.sqlite"


def _utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PaperLedgerError(f"{name} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _utc(value, "timestamp").strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


def _probabilities(values: Mapping[str, float], outcomes: tuple[str, ...]) -> dict[str, float]:
    if not isinstance(values, Mapping) or set(values) != set(outcomes):
        raise PaperLedgerError("probabilities must contain the full outcome set")
    try:
        parsed = {key: float(values[key]) for key in outcomes}
    except (ValueError, TypeError, OverflowError) as error:
        raise PaperLedgerError("probabilities must be numeric") from error
    if any(not isfinite(value) or value < 0 or value > 1 for value in parsed.values()):
        raise PaperLedgerError("probabilities must be finite values from zero to one")
    if not isclose(sum(parsed.values()), 1.0, rel_tol=0, abs_tol=1e-8):
        raise PaperLedgerError("probabilities must sum to one")
    return parsed


class PaperLedger:
    """SQLite event log for observed odds, forecasts, corrections, and finals."""

    def __init__(self, path: Path | str, *, clock: Callable[[], datetime] | None = None):
        self.path = Path(path)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL CHECK (kind IN ('quote', 'prediction', 'kickoff', 'final')),
                    match_id TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_by_match ON events(match_id, kind, recorded_at);
                CREATE UNIQUE INDEX IF NOT EXISTS one_final_per_match
                    ON events(match_id) WHERE kind = 'final';
                CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
                    BEGIN SELECT RAISE(ABORT, 'ledger events are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
                    BEGIN SELECT RAISE(ABORT, 'ledger events are immutable'); END;
            """)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def _now(self) -> datetime:
        return _utc(self.clock(), "clock")

    def _insert(self, kind: str, match_id: str, payload: dict, recorded_at: datetime) -> str:
        event_id = uuid4().hex
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO events(event_id, kind, match_id, recorded_at, payload_json) VALUES (?, ?, ?, ?, ?)",
                (event_id, kind, match_id, _iso(recorded_at),
                 json.dumps(payload, sort_keys=True, separators=(",", ":"))),
            )
        return event_id

    def _event(self, event_id: str, kind: str) -> tuple[sqlite3.Row, dict]:
        with closing(self._connect()) as db, db:
            row = db.execute("SELECT * FROM events WHERE event_id = ? AND kind = ?",
                             (event_id, kind)).fetchone()
        if row is None:
            raise PaperLedgerError(f"{kind} event not found")
        return row, json.loads(row["payload_json"])

    def _events(self, match_id: str, kind: str) -> list[tuple[sqlite3.Row, dict]]:
        with closing(self._connect()) as db, db:
            rows = db.execute(
                "SELECT * FROM events WHERE match_id = ? AND kind = ? ORDER BY recorded_at, rowid",
                (match_id, kind),
            ).fetchall()
        return [(row, json.loads(row["payload_json"])) for row in rows]

    def _kickoff(self, match_id: str, initial: datetime) -> datetime:
        changes = self._events(match_id, "kickoff")
        return datetime.fromisoformat(changes[-1][1]["kickoff_at"]) if changes else initial

    def record_quote(self, quote: OddsQuote) -> str:
        """Append a quote captured now; return its permanent event ID."""

        if not isinstance(quote, OddsQuote):
            raise PaperLedgerError("quote must be an OddsQuote")
        now = self._now()
        if abs((now - quote.recorded_at).total_seconds()) > 300:
            raise PaperLedgerError("quote capture is over five minutes from ledger insertion")
        prior = self._events(quote.match_id, "quote")
        if prior:
            first = prior[0][1]
            if (quote.sport, quote.league, quote.home_team.casefold(), quote.away_team.casefold()) != (
                first["sport"], first["league"], first["home_team"].casefold(),
                first["away_team"].casefold(),
            ):
                raise PaperLedgerError("quote does not match the existing fixture identity")
            if quote.kickoff_at != self._kickoff(
                quote.match_id, datetime.fromisoformat(first["kickoff_at"])
            ):
                raise PaperLedgerError("record a kickoff correction before importing revised odds")
        if now >= self._kickoff(quote.match_id, quote.kickoff_at):
            raise PaperLedgerError("cannot record pre-match odds at or after latest known kickoff")
        payload = {
            "sport": quote.sport, "league": quote.league,
            "home_team": quote.home_team, "away_team": quote.away_team,
            "bookmaker": quote.bookmaker, "decimal_odds": dict(quote.decimal_odds),
            "quote_captured_at": _iso(quote.recorded_at),
            "kickoff_at": _iso(quote.kickoff_at), "source": quote.source,
            "source_kind": quote.source_kind, "label": quote.label,
            "provider_event_id": quote.provider_event_id,
            "bookmaker_updated_at": (
                _iso(quote.bookmaker_updated_at) if quote.bookmaker_updated_at else None
            ),
        }
        return self._insert("quote", quote.match_id, payload, now)

    def record_prediction(
        self, *, quote_id: str, pick: str, probabilities: Mapping[str, float],
        stake_ngn: int, model_version: str,
    ) -> str:
        """Bind a hypothetical pick to the exact already-recorded quote."""

        quote_event, quote = self._event(quote_id, "quote")
        now = self._now()
        if type(stake_ngn) is not int or stake_ngn not in {100, 200}:
            raise PaperLedgerError("hypothetical stake must be NGN 100 or 200")
        if not isinstance(model_version, str) or not model_version.strip():
            raise PaperLedgerError("model_version is required")
        outcomes = outcomes_for(quote["sport"], quote["league"])
        if pick not in outcomes:
            raise PaperLedgerError("pick is not an outcome for this sport")
        probabilities = _probabilities(probabilities, outcomes)
        kickoff_at = self._kickoff(
            quote_event["match_id"], datetime.fromisoformat(quote["kickoff_at"]),
        )
        if now >= kickoff_at:
            raise PaperLedgerError("prediction must be recorded before latest known kickoff")
        if now < datetime.fromisoformat(quote_event["recorded_at"]):
            raise PaperLedgerError("prediction cannot precede its captured odds")
        payload = {
            "quote_id": quote_id, "sport": quote["sport"], "league": quote["league"],
            "home_team": quote["home_team"], "away_team": quote["away_team"],
            "bookmaker": quote["bookmaker"], "pick": pick,
            "probabilities": probabilities, "stake_ngn": stake_ngn,
            "taken_odds": quote["decimal_odds"],
            "quote_recorded_at": quote_event["recorded_at"],
            "kickoff_known_at_prediction": _iso(kickoff_at),
            "model_version": model_version.strip(), "hypothetical": True,
        }
        return self._insert("prediction", quote_event["match_id"], payload, now)

    def record_kickoff_correction(
        self, match_id: str, *, kickoff_at: datetime, source: str,
    ) -> str:
        """Append a corrected start time; old predictions are never edited."""

        if not isinstance(match_id, str) or not match_id.strip():
            raise PaperLedgerError("match_id is required")
        if not isinstance(source, str) or not source.strip():
            raise PaperLedgerError("kickoff correction source is required")
        kickoff_at = _utc(kickoff_at, "kickoff_at")
        if not self._events(match_id, "quote"):
            raise PaperLedgerError("record a quote before correcting its match kickoff")
        return self._insert(
            "kickoff", match_id, {"kickoff_at": _iso(kickoff_at), "source": source.strip()},
            self._now(),
        )

    def settle_from_final(self, match: HistoricalMatch, *, match_id: str | None = None) -> str:
        """Append an imported final; an explicit ID alias must still match teams."""

        if not isinstance(match, HistoricalMatch):
            raise PaperLedgerError("settlement requires an imported HistoricalMatch")
        ledger_match_id = match_id or match.match_id
        quotes = self._events(ledger_match_id, "quote")
        if not quotes:
            raise PaperLedgerError("cannot settle a match without recorded odds")
        for _, quote in quotes:
            if (quote["sport"], quote["league"]) != (match.sport, match.league):
                raise PaperLedgerError("imported final sport/league does not match odds")
            if (quote["home_team"].casefold(), quote["away_team"].casefold()) != (
                match.home_team.casefold(), match.away_team.casefold()
            ):
                raise PaperLedgerError("imported final teams do not match odds; resolve aliases explicitly")
            if abs((datetime.fromisoformat(quote["kickoff_at"]).date() - match.match_date).days) > 1:
                raise PaperLedgerError("imported final date differs from quoted fixture")
        now = self._now()
        if match.result_available_at > now or match.fetched_at > now:
            raise PaperLedgerError("imported final is not yet available at settlement time")
        payload = {
            "history_match_id": match.match_id, "sport": match.sport,
            "league": match.league, "home_team": match.home_team,
            "away_team": match.away_team, "home_score": match.home_score,
            "away_score": match.away_score, "source": match.source,
            "fetched_at": _iso(match.fetched_at),
            "result_available_at": _iso(match.result_available_at),
            "verified_kickoff_at": _iso(match.kickoff_at) if match.kickoff_at else None,
        }
        existing = self._events(ledger_match_id, "final")
        if existing:
            if existing[0][1] == payload:
                return existing[0][0]["event_id"]
            raise PaperLedgerError("conflicting imported final; existing settlement is immutable")
        return self._insert("final", ledger_match_id, payload, now)

    def prediction_report(self, prediction_id: str) -> dict:
        """Recalculate outcome, observed opening/closing prices, and CLV."""

        prediction_event, prediction = self._event(prediction_id, "prediction")
        match_id = prediction_event["match_id"]
        quote_event, quote = self._event(prediction["quote_id"], "quote")
        initial_kickoff = datetime.fromisoformat(quote["kickoff_at"])
        finals = self._events(match_id, "final")
        final = finals[0][1] if finals else None
        corrected = self._kickoff(match_id, initial_kickoff)
        actual_kickoff = (
            datetime.fromisoformat(final["verified_kickoff_at"])
            if final and final["verified_kickoff_at"] else corrected
        )
        kickoff_basis = (
            "imported_final" if final and final["verified_kickoff_at"] else
            "correction" if self._events(match_id, "kickoff") else "initial_quote"
        )
        bookmaker = prediction["bookmaker"]
        valid_quotes = [
            (row, payload) for row, payload in self._events(match_id, "quote")
            if payload["bookmaker"] == bookmaker
            and datetime.fromisoformat(row["recorded_at"]) < actual_kickoff
        ]
        openings = [(row, payload) for row, payload in valid_quotes if payload["label"] == "opening" and
                    row["recorded_at"] <= prediction_event["recorded_at"]]
        closings = [(row, payload) for row, payload in valid_quotes if payload["label"] == "closing" and
                    row["recorded_at"] >= prediction_event["recorded_at"]]
        opening_event, opening = openings[0] if openings else (None, None)
        closing_event, closing = closings[-1] if closings else (None, None)
        pick = prediction["pick"]
        taken_price = float(prediction["taken_odds"][pick])
        outcomes = outcomes_for(prediction["sport"], prediction["league"])
        market_probabilities = devig_decimal_odds(prediction["taken_odds"], outcomes)
        probability_edge = {
            outcome: prediction["probabilities"][outcome] - market_probabilities[outcome]
            for outcome in outcomes
        }
        closing_price = float(closing["decimal_odds"][pick]) if closing else None
        recorded_at = datetime.fromisoformat(prediction_event["recorded_at"])
        if recorded_at >= actual_kickoff:
            status = "void_corrected_kickoff"
            net_ngn = "0.00"
        elif final is None:
            status = "pending"
            net_ngn = None
        else:
            actual = ("home" if final["home_score"] > final["away_score"] else
                      "away" if final["home_score"] < final["away_score"] else "draw")
            status = "win" if pick == actual else "loss"
            stake = Decimal(prediction["stake_ngn"])
            net = (stake * (Decimal(str(taken_price)) - 1) if status == "win" else -stake)
            net_ngn = str(net.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        clv = taken_price / closing_price - 1 if closing_price and status != "void_corrected_kickoff" else None
        return {
            "prediction_id": prediction_id, "match_id": match_id,
            "recorded_at": prediction_event["recorded_at"],
            "kickoff_known_at_prediction": prediction["kickoff_known_at_prediction"],
            "latest_kickoff_at": _iso(actual_kickoff),
            "kickoff_basis": kickoff_basis,
            "pick": pick, "probabilities": prediction["probabilities"],
            "market_implied_probabilities": market_probabilities,
            "model_minus_market_probability": probability_edge,
            "bookmaker": bookmaker, "stake_ngn": prediction["stake_ngn"],
            "hypothetical": True, "model_version": prediction["model_version"],
            "taken_odds": taken_price, "quote_recorded_at": quote_event["recorded_at"],
            "quote_source": quote["source"],
            "opening_odds": float(opening["decimal_odds"][pick]) if opening else None,
            "opening_recorded_at": opening_event["recorded_at"] if opening_event else None,
            "closing_odds": closing_price,
            "closing_recorded_at": closing_event["recorded_at"] if closing_event else None,
            "closing_source": closing["source"] if closing else None,
            "closing_line_value_fraction": clv,
            "closing_line_value_note": (
                "Based on a quote explicitly labeled closing; true market close is not independently verified"
                if clv is not None else None
            ),
            "status": status, "net_ngn": net_ngn,
            "final_score": ([final["home_score"], final["away_score"]] if final else None),
        }

    def list_predictions(self, *, match_id: str | None = None) -> tuple[dict, ...]:
        with closing(self._connect()) as db, db:
            if match_id is None:
                rows = db.execute("SELECT event_id FROM events WHERE kind = 'prediction' "
                                  "ORDER BY recorded_at, rowid").fetchall()
            else:
                rows = db.execute("SELECT event_id FROM events WHERE kind = 'prediction' "
                                  "AND match_id = ? ORDER BY recorded_at, rowid",
                                  (match_id,)).fetchall()
        return tuple(self.prediction_report(row["event_id"]) for row in rows)
