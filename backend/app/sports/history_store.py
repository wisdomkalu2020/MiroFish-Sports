"""Immutable local snapshots of imported historical final scores.

The store makes no network calls. A snapshot contains normalized
``HistoricalMatch`` records, including the caller-supplied source URL and
fetched time. Its SHA-256 digest is its filename; an import never rewrites an
older snapshot. Conflicting finals for one match ID are rejected, including
later score corrections, until a human resolves the source discrepancy.

Identical finals from later imports are deduplicated on load. The earliest
``fetched_at`` record is returned, preserving the oldest available source
provenance for live forecasts. A historical snapshot fetched after a past
match's cutoff is *not* a contemporaneous pre-match dossier snapshot.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import fields
from datetime import date, datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import urlsplit

from app.config import Config

from .history import HistoricalMatch


class HistoryStoreError(ValueError):
    """A historical snapshot is malformed, altered, or contradictory."""


_FIELDS = frozenset(field.name for field in fields(HistoricalMatch))
_SNAPSHOT_NAME = re.compile(r"^history-([0-9a-f]{64})\.jsonl$")


def default_history_root() -> Path:
    """Return the configured local history directory, without creating it."""

    configured = os.environ.get("MIROFISH_SPORTS_HISTORY_DIR", "").strip()
    return Path(configured) if configured else Path(Config.UPLOAD_FOLDER) / "sports_history"


def _source_url(value: str) -> None:
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
    except ValueError as error:
        raise HistoryStoreError("history source must be a valid HTTP(S) URL") from error
    if (parsed.scheme not in {"http", "https"} or not host or
            parsed.username is not None or parsed.password is not None or
            any(character.isspace() for character in value)):
        raise HistoryStoreError("history source must be a valid HTTP(S) URL without credentials")


def _validate(record: HistoricalMatch) -> None:
    if not isinstance(record, HistoricalMatch):
        raise HistoryStoreError("history records must be HistoricalMatch instances")
    _source_url(record.source)


def _sporting_facts(record: HistoricalMatch) -> tuple:
    """Fields that cannot change across imports of the same match ID."""

    return (
        record.sport, record.league, record.season, record.home_team,
        record.away_team, record.match_date, record.kickoff_at,
        record.kickoff_unverified, record.home_score, record.away_score,
        record.result_available_at,
    )


def _add_record(by_id: dict[str, HistoricalMatch], record: HistoricalMatch) -> None:
    previous = by_id.get(record.match_id)
    if previous is not None and _sporting_facts(previous) != _sporting_facts(record):
        raise HistoryStoreError(f"conflicting final for match_id {record.match_id}")
    if previous is None or (record.fetched_at, record.source) < (previous.fetched_at, previous.source):
        by_id[record.match_id] = record


def _record_json(record: HistoricalMatch) -> bytes:
    payload = {}
    for field in fields(HistoricalMatch):
        value = getattr(record, field.name)
        payload[field.name] = value.isoformat() if isinstance(value, (date, datetime)) else value
    return (json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _parse_record(raw: bytes, path: Path, line_number: int) -> HistoricalMatch:
    try:
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict) or set(value) - _FIELDS:
            raise ValueError("invalid record fields")
        value["match_date"] = date.fromisoformat(value["match_date"])
        for field in ("kickoff_at", "result_available_at", "fetched_at"):
            if value.get(field) is not None:
                value[field] = datetime.fromisoformat(value[field])
        record = HistoricalMatch(**value)
        _validate(record)
        return record
    except (KeyError, TypeError, ValueError, UnicodeDecodeError) as error:
        raise HistoryStoreError(f"invalid snapshot {path.name} line {line_number}: {error}") from error


def _load_all(root: Path) -> dict[str, HistoricalMatch]:
    by_id: dict[str, HistoricalMatch] = {}
    if not root.exists():
        return by_id
    if not root.is_dir():
        raise HistoryStoreError("history root must be a directory")
    for path in sorted(root.glob("history-*.jsonl")):
        match = _SNAPSHOT_NAME.fullmatch(path.name)
        if match is None or not path.is_file():
            raise HistoryStoreError(f"unexpected history snapshot {path.name}")
        digest = hashlib.sha256()
        try:
            with path.open("rb") as snapshot:
                for line_number, line in enumerate(snapshot, start=1):
                    digest.update(line)
                    _add_record(by_id, _parse_record(line, path, line_number))
        except OSError as error:
            raise HistoryStoreError(f"cannot read snapshot {path.name}: {error}") from error
        if digest.hexdigest() != match.group(1):
            raise HistoryStoreError(f"snapshot checksum mismatch: {path.name}")
    return by_id


def load_history(root: Path | str, *, sport: str, league: str) -> tuple[HistoricalMatch, ...]:
    """Load one league's immutable finals, rejecting any stored conflicts."""

    if sport not in {"football", "basketball"}:
        raise ValueError("sport must be football or basketball")
    if not isinstance(league, str) or not league.strip():
        raise ValueError("league must be non-empty text")
    by_id = _load_all(Path(root))
    return tuple(sorted(
        (row for row in by_id.values() if row.sport == sport and row.league == league),
        key=lambda row: (row.match_date, row.match_id),
    ))


def save_history(root: Path | str, records: Iterable[HistoricalMatch]) -> Path:
    """Save one content-addressed JSONL snapshot; never overwrite an import.

    Re-importing the same normalized records is idempotent. A later import of
    the same match ID with a changed final score, date, teams, or kickoff is
    rejected, leaving the prior snapshots untouched.
    """

    by_id: dict[str, HistoricalMatch] = {}
    for record in records:
        _validate(record)
        _add_record(by_id, record)
    if not by_id:
        raise HistoryStoreError("history import has no final results")
    root = Path(root)
    for record in _load_all(root).values():
        previous = by_id.get(record.match_id)
        if previous is not None and _sporting_facts(previous) != _sporting_facts(record):
            raise HistoryStoreError(f"conflicting final for match_id {record.match_id}")
    content = b"".join(
        _record_json(by_id[match_id]) for match_id in sorted(by_id)
    )
    destination = root / f"history-{hashlib.sha256(content).hexdigest()}.jsonl"
    if destination.exists():
        if destination.read_bytes() != content:
            raise HistoryStoreError(f"snapshot checksum mismatch: {destination.name}")
        return destination
    root.mkdir(parents=True, exist_ok=True)
    # Build a complete temporary file before exposing the immutable snapshot.
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", prefix=".history-import-", suffix=".tmp",
                                         dir=root, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            # Windows rename fails when the destination exists. POSIX rename
            # replaces it, so use a hard link there instead.
            if os.name == "nt":
                os.rename(temporary, destination)
                temporary = None
            else:
                os.link(temporary, destination)
        except FileExistsError:
            if destination.read_bytes() != content:
                raise HistoryStoreError(f"snapshot checksum mismatch: {destination.name}")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination
