"""Append-only API response captures used for honest historical as-of reads."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


class SnapshotError(RuntimeError):
    """A captured response is missing, corrupted, or malformed."""


class SnapshotUnavailable(SnapshotError):
    """No API response was captured by the requested cutoff."""


def _aware_utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class Snapshot:
    source_url: str
    fetched_at: datetime
    raw_json: bytes

    def payload(self) -> dict:
        try:
            value = json.loads(self.raw_json)
        except (UnicodeError, json.JSONDecodeError) as error:
            raise SnapshotError("Captured API response is not valid JSON") from error
        if not isinstance(value, dict):
            raise SnapshotError("Captured API response is not a JSON object")
        return value


class SnapshotStore:
    """Save exact response bytes with immutable timestamp and source metadata.

    Metadata is the completion marker.  A crash before metadata is written
    leaves an ignored orphan body, never a partly readable historical capture.
    No authentication headers or keys are stored.
    """

    def __init__(self, root: Path | str):
        self.root = Path(root)

    def _url_directory(self, source_url: str) -> Path:
        digest = hashlib.sha256(source_url.encode("utf-8")).hexdigest()
        # The source URL in metadata is checked again on read. A 128-bit
        # directory key avoids Windows path-length failures under long roots.
        return self.root / digest[:32]

    def _legacy_url_directory(self, source_url: str) -> Path:
        """Keep captures written with the original, longer layout readable."""
        digest = hashlib.sha256(source_url.encode("utf-8")).hexdigest()
        return self.root / digest[:2] / digest

    def save(self, source_url: str, raw_json: bytes, fetched_at: datetime) -> Snapshot:
        if not source_url.startswith("https://"):
            raise ValueError("source_url must be an HTTPS URL")
        if not isinstance(raw_json, bytes):
            raise TypeError("raw_json must contain exact response bytes")
        fetched_at = _aware_utc(fetched_at, "fetched_at")
        snapshot = Snapshot(source_url, fetched_at, raw_json)
        snapshot.payload()  # Do not commit malformed captures.
        folder = self._url_directory(source_url)
        folder.mkdir(parents=True, exist_ok=True)
        # The full timestamp remains in immutable metadata; it need not also
        # lengthen every body and metadata filename.
        stem = uuid4().hex
        body_name = f"{stem}.response.json"
        meta_name = f"{stem}.meta.json"
        metadata = {
            "source_url": source_url,
            "fetched_at": fetched_at.isoformat(),
            "body_file": body_name,
            "sha256": hashlib.sha256(raw_json).hexdigest(),
        }
        body_temp = folder / f".{body_name}.tmp"
        meta_temp = folder / f".{meta_name}.tmp"
        try:
            body_temp.write_bytes(raw_json)
            os.replace(body_temp, folder / body_name)
            meta_temp.write_text(json.dumps(metadata, sort_keys=True), encoding="utf-8")
            os.replace(meta_temp, folder / meta_name)
        finally:
            body_temp.unlink(missing_ok=True)
            meta_temp.unlink(missing_ok=True)
        return snapshot

    def latest_as_of(self, source_url: str, cutoff_at: datetime) -> Snapshot | None:
        cutoff_at = _aware_utc(cutoff_at, "cutoff_at")
        selected: tuple[datetime, Path, dict] | None = None
        for folder in (self._url_directory(source_url), self._legacy_url_directory(source_url)):
            if not folder.exists():
                continue
            for meta_path in folder.glob("*.meta.json"):
                try:
                    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
                    if metadata["source_url"] != source_url:
                        raise SnapshotError("Snapshot URL hash collision or metadata mismatch")
                    fetched_at = _aware_utc(
                        datetime.fromisoformat(metadata["fetched_at"]), "fetched_at"
                    )
                except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                    raise SnapshotError("Snapshot metadata is corrupt") from error
                if fetched_at <= cutoff_at and (selected is None or fetched_at > selected[0]):
                    selected = (fetched_at, meta_path, metadata)
        if selected is None:
            return None
        fetched_at, meta_path, metadata = selected
        try:
            body_name = metadata["body_file"]
            checksum = metadata["sha256"]
        except KeyError as error:
            raise SnapshotError("Snapshot metadata is incomplete") from error
        if Path(body_name).name != body_name:
            raise SnapshotError("Snapshot body path is invalid")
        try:
            raw_json = (meta_path.parent / body_name).read_bytes()
        except OSError as error:
            raise SnapshotError("Snapshot response body is missing") from error
        if hashlib.sha256(raw_json).hexdigest() != checksum:
            raise SnapshotError("Snapshot response checksum does not match")
        snapshot = Snapshot(source_url, fetched_at, raw_json)
        snapshot.payload()
        return snapshot
