"""Complete boundary-aware 2024 public source acquisition and verification."""

from __future__ import annotations

import json
import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from arrive90_data_contracts.gates import DEFAULT_ACCEPTANCE_VERSION
from arrive90_data_contracts.source import (
    AcquisitionContentEntry,
    DerivedArtifactEntry,
    InventoryLockEntry,
)

from arrive90_ingestion.acquisition import (
    PARQUET_PARSER_VERSION,
    AcquisitionError,
    acquisition_content_entry,
    download_resumable,
    fetch_http_object_metadata,
    parquet_profile,
    sha256_file,
    sqlite_schema_fingerprint,
    write_acquisition_lock,
)
from arrive90_ingestion.inventory import (
    EXPECTED_OBJECT_COUNT,
    FIRST_BOUNDARY_DATE,
    LAST_BOUNDARY_DATE,
)
from arrive90_ingestion.pinned_sources import BUS_HOST, DEFAULT_RAW_ROOT

DEFAULT_INVENTORY_LOCK = Path("configs/source-locks/mbta-2024.json")
DEFAULT_PINNED_ACQUISITION_LOCK = Path("configs/source-locks/milestone0-acquired.json")
DEFAULT_FULL_ACQUISITION_LOCK = Path("configs/source-locks/mbta-2024-acquired.json")
MEBIBYTE = 1024 * 1024
MAX_WORKERS = 8


@dataclass(frozen=True, slots=True)
class FullYearAcquisitionResult:
    """Verified complete-year source facts emitted by the public CLI."""

    acquisition_lock_path: Path
    acquisition_lock_sha256: str
    object_count: int
    total_size_bytes: int
    total_row_count: int
    schema_fingerprints: tuple[str, ...]
    schedule_database_sha256: str


def _mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise AcquisitionError(f"{field} must be an object with string keys")
    return value


def _load_json(path: Path, field: str) -> dict[str, Any]:
    try:
        return _mapping(json.loads(path.read_text(encoding="utf-8")), field)
    except json.JSONDecodeError as error:
        raise AcquisitionError(f"{field} must be valid JSON") from error


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise AcquisitionError(f"{field} must be a nonempty string")
    return value


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AcquisitionError(f"{field} must be an integer")
    return value


def _number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AcquisitionError(f"{field} must be numeric")
    return float(value)


def _timestamp(value: object, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(_text(value, field).replace("Z", "+00:00"))
    except ValueError as error:
        raise AcquisitionError(f"{field} must be an ISO timestamp") from error
    if parsed.tzinfo is None:
        raise AcquisitionError(f"{field} must be timezone-aware")
    return parsed


def _optional_timestamp(value: object, field: str) -> datetime | None:
    return None if value is None else _timestamp(value, field)


def load_inventory_entries(path: Path, year: int) -> tuple[InventoryLockEntry, ...]:
    if year != 2024:
        raise AcquisitionError("the complete-year acquisition is frozen to 2024")
    lock = _load_json(path, "inventory lock")
    if lock.get("acceptance_version") != DEFAULT_ACCEPTANCE_VERSION:
        raise AcquisitionError("inventory lock has a stale acceptance version")
    raw_entries = lock.get("entries")
    if not isinstance(raw_entries, list) or len(raw_entries) != EXPECTED_OBJECT_COUNT:
        raise AcquisitionError(f"inventory lock must contain {EXPECTED_OBJECT_COUNT} entries")
    entries: list[InventoryLockEntry] = []
    for index, value in enumerate(raw_entries):
        raw = _mapping(value, f"inventory entry {index}")
        try:
            inventory_date = datetime.strptime(
                _text(raw.get("inventory_date"), "inventory_date"), "%Y-%m-%d"
            ).date()
            entry = InventoryLockEntry(
                inventory_snapshot_url=_text(
                    raw.get("inventory_snapshot_url"), "inventory_snapshot_url"
                ),
                inventory_snapshot_sha256=_text(
                    raw.get("inventory_snapshot_sha256"), "inventory_snapshot_sha256"
                ),
                inventory_generated_at=_timestamp(
                    raw.get("inventory_generated_at"), "inventory_generated_at"
                ),
                inventory_date=inventory_date,
                source_object_key=_text(raw.get("source_object_key"), "source_object_key"),
                source_url=_text(raw.get("source_url"), "source_url"),
                declared_size_mb=_number(raw.get("declared_size_mb"), "declared_size_mb"),
            )
        except ValueError as error:
            raise AcquisitionError(f"invalid inventory entry {index}: {error}") from error
        entries.append(entry)
    entries.sort(key=lambda entry: (entry.inventory_date, entry.source_object_key.encode()))
    if entries[0].inventory_date != FIRST_BOUNDARY_DATE:
        raise AcquisitionError("inventory lock is missing the leading boundary object")
    if entries[-1].inventory_date != LAST_BOUNDARY_DATE:
        raise AcquisitionError("inventory lock is missing the trailing boundary object")
    if len({entry.inventory_date for entry in entries}) != EXPECTED_OBJECT_COUNT:
        raise AcquisitionError("inventory lock dates must be unique and complete")
    if len({entry.source_object_key for entry in entries}) != EXPECTED_OBJECT_COUNT:
        raise AcquisitionError("inventory source object keys must be unique")
    return tuple(entries)


def _content_entry(value: object, field: str) -> AcquisitionContentEntry:
    raw = _mapping(value, field)
    try:
        return AcquisitionContentEntry(
            source_object_key=_text(raw.get("source_object_key"), f"{field}.source_object_key"),
            source_url=_text(raw.get("source_url"), f"{field}.source_url"),
            response_size_bytes=_integer(
                raw.get("response_size_bytes"), f"{field}.response_size_bytes"
            ),
            etag=None if raw.get("etag") is None else _text(raw.get("etag"), f"{field}.etag"),
            last_modified_at_utc=_optional_timestamp(
                raw.get("last_modified_at_utc"), f"{field}.last_modified_at_utc"
            ),
            downloaded_at_utc=_timestamp(
                raw.get("downloaded_at_utc"), f"{field}.downloaded_at_utc"
            ),
            sha256=_text(raw.get("sha256"), f"{field}.sha256"),
            schema_fingerprint=_text(raw.get("schema_fingerprint"), f"{field}.schema_fingerprint"),
            row_count=_integer(raw.get("row_count"), f"{field}.row_count"),
            parser_version=_text(raw.get("parser_version"), f"{field}.parser_version"),
        )
    except ValueError as error:
        raise AcquisitionError(f"invalid {field}: {error}") from error


def _derived_entry(value: object, field: str) -> DerivedArtifactEntry:
    raw = _mapping(value, field)
    parameters = raw.get("transformation_parameters")
    if not isinstance(parameters, list):
        raise AcquisitionError(f"{field}.transformation_parameters must be a list")
    try:
        return DerivedArtifactEntry(
            artifact_id=_text(raw.get("artifact_id"), f"{field}.artifact_id"),
            source_content_sha256=_text(
                raw.get("source_content_sha256"), f"{field}.source_content_sha256"
            ),
            transformation_name=_text(
                raw.get("transformation_name"), f"{field}.transformation_name"
            ),
            transformation_version=_text(
                raw.get("transformation_version"), f"{field}.transformation_version"
            ),
            transformation_parameters=tuple(
                (
                    _text(pair[0], f"{field}.transformation_parameters.name"),
                    _text(pair[1], f"{field}.transformation_parameters.value"),
                )
                for pair in parameters
                if isinstance(pair, list) and len(pair) == 2
            ),
            output_size_bytes=_integer(raw.get("output_size_bytes"), f"{field}.output_size_bytes"),
            output_sha256=_text(raw.get("output_sha256"), f"{field}.output_sha256"),
            schema_fingerprint=_text(raw.get("schema_fingerprint"), f"{field}.schema_fingerprint"),
        )
    except ValueError as error:
        raise AcquisitionError(f"invalid {field}: {error}") from error


def load_acquisition_lock(
    path: Path,
    field: str,
) -> tuple[tuple[AcquisitionContentEntry, ...], tuple[DerivedArtifactEntry, ...]]:
    lock = _load_json(path, field)
    if lock.get("acceptance_version") != DEFAULT_ACCEPTANCE_VERSION:
        raise AcquisitionError(f"{field} has a stale acceptance version")
    raw_content = lock.get("content_entries")
    raw_derived = lock.get("derived_entries")
    if not isinstance(raw_content, list) or not isinstance(raw_derived, list):
        raise AcquisitionError(f"{field} entries must be lists")
    return (
        tuple(
            _content_entry(value, f"{field} content entry {index}")
            for index, value in enumerate(raw_content)
        ),
        tuple(
            _derived_entry(value, f"{field} derived entry {index}")
            for index, value in enumerate(raw_derived)
        ),
    )


def _maximum_bytes(entry: InventoryLockEntry) -> int:
    return max(1, math.ceil(entry.declared_size_mb * MEBIBYTE * 1.25))


def _acquire_one(
    entry: InventoryLockEntry,
    *,
    raw_root: Path,
    expected: AcquisitionContentEntry | None,
) -> AcquisitionContentEntry:
    maximum_bytes = _maximum_bytes(entry)
    if expected is None:
        metadata = fetch_http_object_metadata(
            entry.source_url,
            allowed_hosts=frozenset({BUS_HOST}),
            maximum_bytes=maximum_bytes,
        )
        expected_size = metadata.size_bytes
        expected_etag = metadata.etag
        expected_modified = metadata.last_modified_at_utc
        expected_sha256 = None
    else:
        if (
            expected.source_object_key != entry.source_object_key
            or expected.source_url != entry.source_url
        ):
            raise AcquisitionError("acquired-content identity does not match the inventory lock")
        expected_size = expected.response_size_bytes
        expected_etag = expected.etag
        expected_modified = expected.last_modified_at_utc
        expected_sha256 = expected.sha256
        maximum_bytes = max(maximum_bytes, expected_size)
    destination = raw_root / "bus-observatory" / "mbta_all" / Path(entry.source_object_key).name
    result = download_resumable(
        entry.source_url,
        destination,
        allowed_hosts=frozenset({BUS_HOST}),
        maximum_bytes=maximum_bytes,
        expected_size_bytes=expected_size,
        expected_sha256=expected_sha256,
        expected_etag=expected_etag,
        expected_last_modified_at_utc=expected_modified,
    )
    profile = parquet_profile(destination)
    observed = acquisition_content_entry(
        result,
        source_object_key=entry.source_object_key,
        source_url=entry.source_url,
        schema_fingerprint=profile.schema_fingerprint,
        row_count=profile.row_count,
        parser_version=PARQUET_PARSER_VERSION,
    )
    if expected is not None:
        stable_observed = (
            observed.source_object_key,
            observed.source_url,
            observed.response_size_bytes,
            observed.etag,
            observed.last_modified_at_utc,
            observed.sha256,
            observed.schema_fingerprint,
            observed.row_count,
            observed.parser_version,
        )
        stable_expected = (
            expected.source_object_key,
            expected.source_url,
            expected.response_size_bytes,
            expected.etag,
            expected.last_modified_at_utc,
            expected.sha256,
            expected.schema_fingerprint,
            expected.row_count,
            expected.parser_version,
        )
        if stable_observed != stable_expected:
            raise AcquisitionError(
                "downloaded object does not match its acquired-content lock: "
                f"{entry.source_object_key}"
            )
        return expected
    return observed


def _verify_schedule(
    raw_root: Path,
    pinned_content: tuple[AcquisitionContentEntry, ...],
    pinned_derived: tuple[DerivedArtifactEntry, ...],
) -> tuple[AcquisitionContentEntry, DerivedArtifactEntry]:
    schedule = tuple(
        entry for entry in pinned_content if entry.source_object_key.endswith("GTFS_ARCHIVE.db.gz")
    )
    if len(schedule) != 1 or len(pinned_derived) != 1:
        raise AcquisitionError(
            "pinned acquisition lock must contain one schedule and one derivative"
        )
    schedule_entry = schedule[0]
    derived_entry = pinned_derived[0]
    archive = raw_root / "mbta-gtfs/2024/GTFS_ARCHIVE.db.gz"
    database = raw_root / "mbta-gtfs/2024/GTFS_ARCHIVE.db"
    if not archive.is_file() or not database.is_file():
        raise AcquisitionError("pinned schedule sources are missing; acquire Milestone 0 first")
    if (
        archive.stat().st_size != schedule_entry.response_size_bytes
        or sha256_file(archive) != schedule_entry.sha256
        or database.stat().st_size != derived_entry.output_size_bytes
        or sha256_file(database) != derived_entry.output_sha256
        or sqlite_schema_fingerprint(database) != derived_entry.schema_fingerprint
    ):
        raise AcquisitionError("pinned schedule source or expanded database failed verification")
    return schedule_entry, derived_entry


def acquire_full_year(
    year: int,
    *,
    inventory_lock_path: Path = DEFAULT_INVENTORY_LOCK,
    pinned_acquisition_lock_path: Path = DEFAULT_PINNED_ACQUISITION_LOCK,
    raw_root: Path = DEFAULT_RAW_ROOT,
    acquisition_lock_path: Path = DEFAULT_FULL_ACQUISITION_LOCK,
    workers: int = 4,
) -> FullYearAcquisitionResult:
    """Acquire or reverify all boundary-aware 2024 objects plus the schedule archive."""

    if not 1 <= workers <= MAX_WORKERS:
        raise AcquisitionError(f"workers must be between 1 and {MAX_WORKERS}")
    inventory_entries = load_inventory_entries(inventory_lock_path, year)
    pinned_content, pinned_derived = load_acquisition_lock(
        pinned_acquisition_lock_path, "pinned acquisition lock"
    )
    schedule_entry, schedule_derived = _verify_schedule(raw_root, pinned_content, pinned_derived)
    locked_content: tuple[AcquisitionContentEntry, ...] | None = None
    locked_derived: tuple[DerivedArtifactEntry, ...] | None = None
    if acquisition_lock_path.is_file():
        locked_content, locked_derived = load_acquisition_lock(
            acquisition_lock_path, "full-year acquisition lock"
        )
        if locked_derived != (schedule_derived,):
            raise AcquisitionError("full-year schedule derivative does not match the pinned lock")
        locked_schedule = tuple(
            entry
            for entry in locked_content
            if entry.source_object_key.endswith("GTFS_ARCHIVE.db.gz")
        )
        if locked_schedule != (schedule_entry,):
            raise AcquisitionError("full-year schedule content does not match the pinned lock")
    expected_by_key = (
        {
            entry.source_object_key: entry
            for entry in locked_content
            if entry.source_object_key.endswith(".parquet")
        }
        if locked_content is not None
        else {}
    )
    inventory_keys = {entry.source_object_key for entry in inventory_entries}
    if locked_content is not None and set(expected_by_key) != inventory_keys:
        raise AcquisitionError("full-year acquired-content keys do not match the inventory lock")

    def acquire(entry: InventoryLockEntry) -> AcquisitionContentEntry:
        return _acquire_one(
            entry,
            raw_root=raw_root,
            expected=expected_by_key.get(entry.source_object_key),
        )

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="arrive90-download") as pool:
        vehicle_entries = tuple(pool.map(acquire, inventory_entries))
    vehicle_entries = tuple(
        sorted(vehicle_entries, key=lambda entry: entry.source_object_key.encode())
    )
    if locked_content is None:
        write_acquisition_lock(
            acquisition_lock_path,
            content_entries=(*vehicle_entries, schedule_entry),
            derived_entries=(schedule_derived,),
        )
    return FullYearAcquisitionResult(
        acquisition_lock_path=acquisition_lock_path,
        acquisition_lock_sha256=sha256_file(acquisition_lock_path),
        object_count=len(vehicle_entries),
        total_size_bytes=sum(entry.response_size_bytes for entry in vehicle_entries),
        total_row_count=sum(entry.row_count for entry in vehicle_entries),
        schema_fingerprints=tuple(
            sorted({entry.schema_fingerprint for entry in vehicle_entries}, key=str.encode)
        ),
        schedule_database_sha256=schedule_derived.output_sha256,
    )
