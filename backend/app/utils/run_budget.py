"""Atomic, cross-process token and estimated-cost limits for one MiroFish run.

All LLM routes for a run must use the same SQLite file and run ID.  Reservations
are made before a model request is sent so concurrent OASIS workers cannot each
spend the same remaining budget.  Unknown outcomes retain their full reservation.
"""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import closing
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path
from typing import Any


_NANOUSD = Decimal("1000000000")


class BudgetExceeded(RuntimeError):
    """A run has reached its token or estimated-cost limit."""


def _limit_nanousd(value: float | str | Decimal | None) -> int | None:
    if value is None:
        return None
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0:
        raise ValueError("cost_limit_usd must be finite and nonnegative")
    return int((amount * _NANOUSD).to_integral_value(rounding=ROUND_FLOOR))


def _cost_nanousd(tokens: int, price_per_1k_usd: Decimal) -> int:
    if tokens == 0 or price_per_1k_usd == 0:
        return 0
    amount = Decimal(tokens) * price_per_1k_usd * _NANOUSD / 1000
    return int(amount.to_integral_value(rounding=ROUND_CEILING))


class RunBudget:
    """SQLite ledger shared by every model call belonging to a run.

    ``run_id`` is an opaque identifier, not an authentication credential.  The
    loopback gateway has its own bearer token.  Existing limits cannot be
    silently increased by constructing another instance for the same run.
    """

    def __init__(
        self,
        db_path: str | Path,
        run_id: str,
        token_limit: int,
        cost_limit_usd: float | str | Decimal | None = None,
        input_cost_per_1k_usd: float | str | Decimal = 0,
        output_cost_per_1k_usd: float | str | Decimal = 0,
    ) -> None:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id must be nonempty")
        if not isinstance(token_limit, int) or isinstance(token_limit, bool) or token_limit <= 0:
            raise ValueError("token_limit must be a positive integer")
        self.db_path = str(Path(db_path).resolve())
        self.run_id = run_id
        self.token_limit = token_limit
        self.cost_limit_nanousd = _limit_nanousd(cost_limit_usd)
        self.input_price = Decimal(str(input_cost_per_1k_usd))
        self.output_price = Decimal(str(output_cost_per_1k_usd))
        for price in (self.input_price, self.output_price):
            if not price.is_finite() or price < 0:
                raise ValueError("token prices must be finite and nonnegative")
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    token_limit INTEGER NOT NULL,
                    cost_limit_nanousd INTEGER,
                    input_price TEXT NOT NULL,
                    output_price TEXT NOT NULL,
                    spent_tokens INTEGER NOT NULL DEFAULT 0,
                    reserved_tokens INTEGER NOT NULL DEFAULT 0,
                    spent_cost_nanousd INTEGER NOT NULL DEFAULT 0,
                    reserved_cost_nanousd INTEGER NOT NULL DEFAULT 0,
                    exhausted INTEGER NOT NULL DEFAULT 0
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS reservations (
                    reservation_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    prompt_estimate INTEGER NOT NULL,
                    output_cap INTEGER NOT NULL,
                    reserved_cost_nanousd INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    actual_prompt_tokens INTEGER,
                    actual_output_tokens INTEGER
                )"""
            )
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (self.run_id,)).fetchone()
            if row is None:
                connection.execute(
                    """INSERT INTO runs
                    (run_id, token_limit, cost_limit_nanousd, input_price, output_price)
                    VALUES (?, ?, ?, ?, ?)""",
                    (
                        self.run_id,
                        self.token_limit,
                        self.cost_limit_nanousd,
                        str(self.input_price),
                        str(self.output_price),
                    ),
                )
            elif (
                row["token_limit"] != self.token_limit
                or row["cost_limit_nanousd"] != self.cost_limit_nanousd
                or row["input_price"] != str(self.input_price)
                or row["output_price"] != str(self.output_price)
            ):
                raise ValueError("existing run budget has different limits or prices")

    def _run_row(self, connection: sqlite3.Connection) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (self.run_id,)).fetchone()
        if row is None:
            raise RuntimeError("run budget is missing")
        return row

    def reserve(self, prompt_tokens_estimate: int, requested_output_tokens: int) -> str:
        """Reserve the full worst-case request before the gateway sends it."""
        for value in (prompt_tokens_estimate, requested_output_tokens):
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError("token estimates and output caps must be positive integers")
        requested_tokens = prompt_tokens_estimate + requested_output_tokens
        requested_cost = _cost_nanousd(prompt_tokens_estimate, self.input_price) + _cost_nanousd(
            requested_output_tokens, self.output_price
        )
        reservation_id = uuid.uuid4().hex
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._run_row(connection)
            exceeds_tokens = (
                row["spent_tokens"] + row["reserved_tokens"] + requested_tokens > row["token_limit"]
            )
            exceeds_cost = (
                row["cost_limit_nanousd"] is not None
                and row["spent_cost_nanousd"] + row["reserved_cost_nanousd"] + requested_cost
                > row["cost_limit_nanousd"]
            )
            if row["exhausted"] or exceeds_tokens or exceeds_cost:
                connection.execute("UPDATE runs SET exhausted = 1 WHERE run_id = ?", (self.run_id,))
                # Preserve the terminal state even though the caller gets an exception.
                connection.commit()
                raise BudgetExceeded("per-run LLM token or cost budget exhausted")
            connection.execute(
                """INSERT INTO reservations
                (reservation_id, run_id, prompt_estimate, output_cap, reserved_cost_nanousd, status)
                VALUES (?, ?, ?, ?, ?, 'pending')""",
                (
                    reservation_id,
                    self.run_id,
                    prompt_tokens_estimate,
                    requested_output_tokens,
                    requested_cost,
                ),
            )
            connection.execute(
                """UPDATE runs SET reserved_tokens = reserved_tokens + ?,
                   reserved_cost_nanousd = reserved_cost_nanousd + ? WHERE run_id = ?""",
                (requested_tokens, requested_cost, self.run_id),
            )
        return reservation_id

    def reconcile(self, reservation_id: str, prompt_tokens: int, completion_tokens: int) -> None:
        """Commit provider-reported usage; any overrun blocks later requests."""
        for value in (prompt_tokens, completion_tokens):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError("actual token usage must be nonnegative integers")
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            reservation = self._pending_reservation(connection, reservation_id)
            reserved_tokens = reservation["prompt_estimate"] + reservation["output_cap"]
            actual_tokens = prompt_tokens + completion_tokens
            actual_cost = _cost_nanousd(prompt_tokens, self.input_price) + _cost_nanousd(
                completion_tokens, self.output_price
            )
            connection.execute(
                """UPDATE runs SET reserved_tokens = reserved_tokens - ?,
                    reserved_cost_nanousd = reserved_cost_nanousd - ?,
                    spent_tokens = spent_tokens + ?,
                    spent_cost_nanousd = spent_cost_nanousd + ? WHERE run_id = ?""",
                (
                    reserved_tokens,
                    reservation["reserved_cost_nanousd"],
                    actual_tokens,
                    actual_cost,
                    self.run_id,
                ),
            )
            connection.execute(
                """UPDATE reservations SET status = 'complete',
                    actual_prompt_tokens = ?, actual_output_tokens = ?
                    WHERE reservation_id = ?""",
                (prompt_tokens, completion_tokens, reservation_id),
            )
            if prompt_tokens > reservation["prompt_estimate"] or completion_tokens > reservation["output_cap"]:
                # The provider exceeded the quantity reserved before dispatch.
                # Existing calls cannot be undone; no new requests may be sent.
                connection.execute("UPDATE runs SET exhausted = 1 WHERE run_id = ?", (self.run_id,))
            self._mark_if_limit_reached(connection)

    def mark_uncertain(self, reservation_id: str) -> None:
        """Charge the complete reservation and stop the run if usage is unknown."""
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            reservation = self._pending_reservation(connection, reservation_id)
            reserved_tokens = reservation["prompt_estimate"] + reservation["output_cap"]
            connection.execute(
                """UPDATE runs SET reserved_tokens = reserved_tokens - ?,
                    reserved_cost_nanousd = reserved_cost_nanousd - ?,
                    spent_tokens = spent_tokens + ?,
                    spent_cost_nanousd = spent_cost_nanousd + ?,
                    exhausted = 1 WHERE run_id = ?""",
                (
                    reserved_tokens,
                    reservation["reserved_cost_nanousd"],
                    reserved_tokens,
                    reservation["reserved_cost_nanousd"],
                    self.run_id,
                ),
            )
            connection.execute(
                "UPDATE reservations SET status = 'uncertain' WHERE reservation_id = ?",
                (reservation_id,),
            )

    def _pending_reservation(self, connection: sqlite3.Connection, reservation_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM reservations WHERE reservation_id = ? AND run_id = ?",
            (reservation_id, self.run_id),
        ).fetchone()
        if row is None or row["status"] != "pending":
            raise ValueError("unknown or already reconciled reservation")
        return row

    def _mark_if_limit_reached(self, connection: sqlite3.Connection) -> None:
        row = self._run_row(connection)
        if (
            row["spent_tokens"] >= row["token_limit"]
            or row["cost_limit_nanousd"] is not None
            and row["cost_limit_nanousd"] > 0
            and row["spent_cost_nanousd"] >= row["cost_limit_nanousd"]
        ):
            connection.execute("UPDATE runs SET exhausted = 1 WHERE run_id = ?", (self.run_id,))

    def snapshot(self) -> dict[str, Any]:
        """Read the current ledger, including in-flight reservations."""
        with closing(self._connect()) as connection, connection:
            row = self._run_row(connection)
            pending = connection.execute(
                "SELECT COUNT(*) FROM reservations WHERE run_id = ? AND status = 'pending'",
                (self.run_id,),
            ).fetchone()[0]
            return {
                "run_id": self.run_id,
                "token_limit": row["token_limit"],
                "spent_tokens": row["spent_tokens"],
                "reserved_tokens": row["reserved_tokens"],
                "pending_requests": pending,
                "cost_limit_usd": None
                if row["cost_limit_nanousd"] is None
                else row["cost_limit_nanousd"] / 1_000_000_000,
                "spent_cost_usd": row["spent_cost_nanousd"] / 1_000_000_000,
                "reserved_cost_usd": row["reserved_cost_nanousd"] / 1_000_000_000,
                "exhausted": bool(row["exhausted"]),
            }

    def ensure_not_exhausted(self) -> None:
        if self.snapshot()["exhausted"]:
            raise BudgetExceeded("per-run LLM token or cost budget exhausted")

    def ensure_settled(self) -> None:
        """Use after child workers exit to reject missing usage or lost replies."""
        state = self.snapshot()
        if state["exhausted"] or state["pending_requests"]:
            raise BudgetExceeded("per-run LLM budget is exhausted or has unreconciled requests")
