"""Create-only, hash-verified SQLite backup and restore operations."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

_EXPECTED_TABLES = frozenset({"decisions", "events", "idempotency", "recovery_decisions", "trips"})


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _inspect(connection: sqlite3.Connection) -> tuple[tuple[str, ...], dict[str, int]]:
    integrity = connection.execute("PRAGMA integrity_check").fetchone()
    if integrity is None or integrity[0] != "ok":
        raise ValueError("SQLite integrity check failed")
    tables = tuple(
        sorted(
            (
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_schema "
                    "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                )
            ),
            key=str.encode,
        )
    )
    if frozenset(tables) != _EXPECTED_TABLES:
        raise ValueError("backup schema does not match the service state schema")
    counts = {
        # Names came from the exact expected schema allow-list above.
        table: int(
            connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]  # noqa: S608
        )
        for table in tables
    }
    return tables, counts


@dataclass(frozen=True)
class BackupManifest:
    schema_version: str
    created_at_utc: str
    backup_sha256: str
    tables: tuple[str, ...]
    row_counts: dict[str, int]
    plaintext_secrets_stored: bool = False
    rider_identity_or_coordinates_stored: bool = False


def create_backup(
    source: Path,
    destination: Path,
    manifest_path: Path,
    *,
    created_at_utc: str,
    expire_before_epoch: float,
) -> BackupManifest:
    """Purge expired state, copy one consistent snapshot, and bind its digest."""

    created_at = datetime.fromisoformat(created_at_utc.replace("Z", "+00:00"))
    if created_at.tzinfo is None or created_at.utcoffset() != UTC.utcoffset(created_at):
        raise ValueError("backup creation time must be explicit UTC")
    if not source.is_file():
        raise FileNotFoundError("state database does not exist")
    if destination.exists() or manifest_path.exists():
        raise FileExistsError("backup destinations are create-only")
    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    source_connection = sqlite3.connect(source)
    destination_connection: sqlite3.Connection | None = None
    try:
        source_connection.execute("PRAGMA foreign_keys = ON")
        source_connection.execute(
            "DELETE FROM decisions WHERE expires_at <= ?", (expire_before_epoch,)
        )
        source_connection.execute("DELETE FROM trips WHERE expires_at <= ?", (expire_before_epoch,))
        source_connection.commit()
        _inspect(source_connection)
        destination_connection = sqlite3.connect(destination)
        source_connection.backup(destination_connection)
        destination_connection.commit()
        tables, counts = _inspect(destination_connection)
    except Exception:
        if destination_connection is not None:
            destination_connection.close()
        source_connection.close()
        destination.unlink(missing_ok=True)
        raise
    destination_connection.close()
    source_connection.close()
    os.chmod(destination, 0o600)
    manifest = BackupManifest(
        "arrive90-state-backup-v1",
        created_at_utc,
        _digest(destination),
        tables,
        counts,
    )
    with manifest_path.open("x", encoding="utf-8") as stream:
        json.dump(asdict(manifest), stream, indent=2, sort_keys=True)
        stream.write("\n")
    return manifest


def restore_backup(backup: Path, manifest_path: Path, destination: Path) -> BackupManifest:
    """Verify a backup before restoring it to a new create-only state path."""

    if destination.exists():
        raise FileExistsError("restore destination is create-only")
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest = BackupManifest(
        **{
            **value,
            "tables": tuple(value["tables"]),
            "row_counts": {str(key): int(count) for key, count in value["row_counts"].items()},
        }
    )
    if manifest.schema_version != "arrive90-state-backup-v1":
        raise ValueError("backup manifest schema is unsupported")
    if manifest.plaintext_secrets_stored or manifest.rider_identity_or_coordinates_stored:
        raise ValueError("backup manifest violates the sensitive-data contract")
    if _digest(backup) != manifest.backup_sha256:
        raise ValueError("backup digest does not match its manifest")
    source_connection = sqlite3.connect(f"file:{backup}?mode=ro", uri=True)
    tables, counts = _inspect(source_connection)
    if tables != manifest.tables or counts != manifest.row_counts:
        source_connection.close()
        raise ValueError("backup contents do not match the manifest inventory")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination_connection: sqlite3.Connection | None = None
    try:
        destination_connection = sqlite3.connect(destination)
        source_connection.backup(destination_connection)
        destination_connection.commit()
        _inspect(destination_connection)
    except Exception:
        if destination_connection is not None:
            destination_connection.close()
        source_connection.close()
        destination.unlink(missing_ok=True)
        raise
    destination_connection.close()
    source_connection.close()
    os.chmod(destination, 0o600)
    return manifest
