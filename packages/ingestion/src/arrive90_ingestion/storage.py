"""Filesystem blob storage with a transactional SQLite fetch-attempt ledger."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
from contextlib import suppress
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from arrive90_data_contracts.realtime import FeedBlob, FetchAttempt


class QuotaExceededError(RuntimeError):
    """Raised before a new immutable blob would exceed a frozen quota."""


class ImmutableAttemptStore:
    """Persist content-addressed blobs and every independent fetch attempt."""

    def __init__(self, root: Path, *, daily_quota_bytes: int, total_quota_bytes: int) -> None:
        if daily_quota_bytes <= 0 or total_quota_bytes <= 0:
            raise ValueError("quotas must be positive")
        self.root = root
        self.daily_quota_bytes = daily_quota_bytes
        self.total_quota_bytes = total_quota_bytes
        self.blob_root = root / "blobs"
        self.database = root / "attempts.sqlite3"
        self.blob_root.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS feed_blobs (
                    blob_sha256 TEXT PRIMARY KEY,
                    content_type TEXT NOT NULL,
                    content_length INTEGER NOT NULL CHECK (content_length >= 0),
                    storage_uri TEXT NOT NULL,
                    first_seen_at_utc TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS fetch_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    parent_attempt_id TEXT,
                    agency_id TEXT NOT NULL,
                    feed_type TEXT NOT NULL,
                    source_object TEXT NOT NULL,
                    fetched_at_utc TEXT NOT NULL,
                    source_header_timestamp TEXT,
                    maximum_entity_timestamp TEXT,
                    http_status INTEGER,
                    blob_sha256 TEXT REFERENCES feed_blobs(blob_sha256),
                    parser_version TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    feed_age_seconds INTEGER,
                    transport_status TEXT NOT NULL,
                    parse_status TEXT NOT NULL,
                    semantic_status TEXT NOT NULL,
                    freshness_status TEXT NOT NULL,
                    failure_code TEXT
                );
                """
            )

    def _blob_path(self, digest: str) -> Path:
        return self.blob_root / digest[:2] / digest[2:]

    def _quota_usage(self, connection: sqlite3.Connection, fetched_at: datetime) -> tuple[int, int]:
        day = fetched_at.date().isoformat()
        total = connection.execute(
            "SELECT COALESCE(SUM(content_length), 0) FROM feed_blobs"
        ).fetchone()[0]
        daily = connection.execute(
            """
            SELECT COALESCE(SUM(content_length), 0)
            FROM feed_blobs
            WHERE substr(first_seen_at_utc, 1, 10) = ?
            """,
            (day,),
        ).fetchone()[0]
        return int(daily), int(total)

    def _write_blob(self, path: Path, body: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            return
        descriptor, temporary_name = tempfile.mkstemp(prefix="blob-", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            with suppress(FileExistsError):
                os.link(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def record(
        self, attempt: FetchAttempt, body: bytes | None, content_type: str
    ) -> FeedBlob | None:
        """Atomically acknowledge an attempt after retaining any accepted body."""

        blob: FeedBlob | None = None
        with self._connect() as connection:
            if connection.execute(
                "SELECT 1 FROM fetch_attempts WHERE attempt_id = ?", (attempt.attempt_id,)
            ).fetchone():
                raise sqlite3.IntegrityError("attempt_id is immutable and already exists")
            if body is not None:
                digest = hashlib.sha256(body).hexdigest()
                if attempt.blob_sha256 != digest:
                    raise ValueError("attempt blob digest does not match body")
                row = connection.execute(
                    "SELECT * FROM feed_blobs WHERE blob_sha256 = ?", (digest,)
                ).fetchone()
                if row is None:
                    daily, total = self._quota_usage(connection, attempt.fetched_at_utc)
                    if daily + len(body) > self.daily_quota_bytes:
                        raise QuotaExceededError("daily immutable object quota exceeded")
                    if total + len(body) > self.total_quota_bytes:
                        raise QuotaExceededError("total immutable object quota exceeded")
                    path = self._blob_path(digest)
                    self._write_blob(path, body)
                    blob = FeedBlob(
                        blob_sha256=digest,
                        content_type=content_type,
                        content_length=len(body),
                        storage_uri=path.resolve().as_uri(),
                        first_seen_at_utc=attempt.fetched_at_utc,
                    )
                    connection.execute(
                        "INSERT INTO feed_blobs VALUES (?, ?, ?, ?, ?)",
                        (
                            blob.blob_sha256,
                            blob.content_type,
                            blob.content_length,
                            blob.storage_uri,
                            blob.first_seen_at_utc.isoformat(),
                        ),
                    )
                else:
                    blob = self._blob_from_row(row)
            values = asdict(attempt)

            def sql_value(value: object) -> object:
                if isinstance(value, datetime):
                    return value.isoformat()
                return value.value if hasattr(value, "value") else value

            connection.execute(
                """
                INSERT INTO fetch_attempts VALUES (
                    :attempt_id, :parent_attempt_id, :agency_id, :feed_type, :source_object,
                    :fetched_at_utc, :source_header_timestamp, :maximum_entity_timestamp,
                    :http_status, :blob_sha256, :parser_version, :schema_version,
                    :feed_age_seconds, :transport_status, :parse_status, :semantic_status,
                    :freshness_status, :failure_code
                )
                """,
                {key: sql_value(value) for key, value in values.items()},
            )
        return blob

    @staticmethod
    def _blob_from_row(row: sqlite3.Row) -> FeedBlob:
        return FeedBlob(
            blob_sha256=row["blob_sha256"],
            content_type=row["content_type"],
            content_length=row["content_length"],
            storage_uri=row["storage_uri"],
            first_seen_at_utc=datetime.fromisoformat(row["first_seen_at_utc"]),
        )

    def blobs(self) -> list[FeedBlob]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM feed_blobs ORDER BY blob_sha256").fetchall()
        return [self._blob_from_row(row) for row in rows]

    def attempts(self) -> list[sqlite3.Row]:
        with self._connect() as connection:
            return connection.execute(
                "SELECT * FROM fetch_attempts ORDER BY fetched_at_utc, attempt_id"
            ).fetchall()
