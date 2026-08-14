"""Milestone 0 orchestration for the pinned public vehicle and schedule sources."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml
from arrive90_data_contracts.gates import DEFAULT_ACCEPTANCE_VERSION

from arrive90_ingestion.acquisition import (
    GZIP_EXPANSION_VERSION,
    MAX_SCHEDULE_DATABASE_BYTES,
    PARQUET_PARSER_VERSION,
    SCHEDULE_PARSER_VERSION,
    AcquisitionError,
    acquisition_content_entry,
    download_resumable,
    expand_gzip_bounded,
    parquet_profile,
    schedule_derived_entry,
    sqlite_schema_fingerprint,
    write_acquisition_lock,
)
from arrive90_ingestion.historical import canonical_json_bytes

DEFAULT_INVENTORY_LOCK = Path("configs/source-locks/mbta-2024.json")
DEFAULT_BUS_PROFILE = Path("configs/sources/bus-observatory-mbta-2024.yaml")
DEFAULT_SCHEDULE_PROFILE = Path("configs/sources/mbta-gtfs-archive-2024.yaml")
DEFAULT_ACQUISITION_LOCK = Path("configs/source-locks/milestone0-acquired.json")
DEFAULT_RAW_ROOT = Path("data/raw")
BUS_HOST = "busobservatory-lake.s3.amazonaws.com"
SCHEDULE_HOST = "performancedata.mbta.com"


@dataclass(frozen=True, slots=True)
class PinnedSourceResult:
    """Paths and immutable hashes emitted by one pinned-day source acquisition."""

    vehicle_path: Path
    vehicle_sha256: str
    vehicle_schema_fingerprint: str
    vehicle_row_count: int
    schedule_archive_path: Path | None
    schedule_archive_sha256: str | None
    schedule_database_path: Path | None
    schedule_database_sha256: str | None
    schedule_schema_fingerprint: str | None
    acquisition_lock_path: Path
    acquisition_lock_sha256: str


def _mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise AcquisitionError(f"{field} must be a JSON or YAML object with string keys")
    return value


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise AcquisitionError(f"{field} must be a nonempty string")
    return value


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise AcquisitionError(f"{field} must be a positive integer")
    return value


def _date(value: object, field: str) -> date:
    if isinstance(value, datetime):
        raise AcquisitionError(f"{field} must be a date without a time")
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(_string(value, field))
    except ValueError as error:
        raise AcquisitionError(f"{field} must be an ISO date") from error


def _optional_sha256(value: object, field: str) -> str | None:
    if value is None:
        return None
    digest = _string(value, field)
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise AcquisitionError(f"{field} must be a lowercase hexadecimal SHA-256")
    return digest


def _optional_positive_integer(value: object, field: str) -> int | None:
    if value is None:
        return None
    return _integer(value, field)


def _load_yaml(path: Path, field: str) -> dict[str, Any]:
    return _mapping(yaml.safe_load(path.read_text(encoding="utf-8")), field)


def _load_json(path: Path, field: str) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise AcquisitionError(f"{field} must be valid JSON") from error
    return _mapping(loaded, field)


def _require_acceptance_version(envelope: dict[str, Any], field: str) -> None:
    if envelope.get("acceptance_version") != DEFAULT_ACCEPTANCE_VERSION:
        raise AcquisitionError(f"{field} acceptance version is not {DEFAULT_ACCEPTANCE_VERSION}")


def _utc(value: object, field: str) -> datetime:
    parsed = datetime.fromisoformat(_string(value, field).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise AcquisitionError(f"{field} must be timezone-aware UTC")
    return parsed


def _inventory_entry(lock: dict[str, Any], inventory_date: date) -> dict[str, Any]:
    entries = lock.get("entries")
    if not isinstance(entries, list):
        raise AcquisitionError("inventory lock entries must be a list")
    matches = [
        _mapping(entry, "inventory lock entry")
        for entry in entries
        if isinstance(entry, dict) and entry.get("inventory_date") == inventory_date.isoformat()
    ]
    if len(matches) != 1:
        raise AcquisitionError(
            "inventory lock must contain exactly one object for the requested date"
        )
    return matches[0]


def acquire_pinned_day(
    inventory_date: date,
    *,
    include_schedule: bool,
    inventory_lock_path: Path = DEFAULT_INVENTORY_LOCK,
    bus_profile_path: Path = DEFAULT_BUS_PROFILE,
    schedule_profile_path: Path = DEFAULT_SCHEDULE_PROFILE,
    raw_root: Path = DEFAULT_RAW_ROOT,
    acquisition_lock_path: Path = DEFAULT_ACQUISITION_LOCK,
) -> PinnedSourceResult:
    """Acquire and content-lock the Milestone 0 real-source inputs."""

    inventory_lock = _load_json(inventory_lock_path, "inventory lock")
    _require_acceptance_version(inventory_lock, "inventory lock")
    entry = _inventory_entry(inventory_lock, inventory_date)
    bus_profile = _load_yaml(bus_profile_path, "Bus Observatory source profile")
    _require_acceptance_version(bus_profile, "Bus Observatory source profile")
    sample = _mapping(bus_profile.get("sample"), "Bus Observatory sample")
    sample_date = _date(sample.get("inventory_date"), "sample.inventory_date")
    if inventory_date != sample_date:
        raise AcquisitionError("Milestone 0 source download is frozen to the pinned sample date")
    source_url = _string(entry.get("source_url"), "inventory source_url")
    source_object_key = _string(entry.get("source_object_key"), "inventory source_object_key")
    if source_url != _string(sample.get("url"), "sample.url"):
        raise AcquisitionError("sample URL does not match the canonical inventory lock")
    expected_vehicle_size = _integer(sample.get("size_bytes"), "sample.size_bytes")
    expected_vehicle_sha256 = _optional_sha256(sample.get("sha256"), "sample.sha256")
    declared_size_mb = entry.get("declared_size_mb")
    if isinstance(declared_size_mb, bool) or not isinstance(declared_size_mb, int | float):
        raise AcquisitionError("inventory declared_size_mb must be numeric")
    maximum_vehicle_bytes = max(
        expected_vehicle_size,
        math.ceil(float(declared_size_mb) * 1024 * 1024 * 1.25),
    )
    vehicle_path = raw_root / "bus-observatory" / "mbta_all" / Path(source_object_key).name
    vehicle_download = download_resumable(
        source_url,
        vehicle_path,
        allowed_hosts=frozenset({BUS_HOST}),
        maximum_bytes=maximum_vehicle_bytes,
        expected_size_bytes=expected_vehicle_size,
        expected_sha256=expected_vehicle_sha256,
    )
    vehicle_profile = parquet_profile(vehicle_path)
    if vehicle_profile.row_count != _integer(sample.get("row_count"), "sample.row_count"):
        raise AcquisitionError("pinned sample Parquet row count does not match the source profile")
    expected_vehicle_schema = _optional_sha256(
        sample.get("schema_fingerprint"), "sample.schema_fingerprint"
    )
    if (
        expected_vehicle_schema is not None
        and vehicle_profile.schema_fingerprint != expected_vehicle_schema
    ):
        raise AcquisitionError("pinned sample Parquet schema does not match the source profile")
    content_entries = [
        acquisition_content_entry(
            vehicle_download,
            source_object_key=source_object_key,
            source_url=source_url,
            schema_fingerprint=vehicle_profile.schema_fingerprint,
            row_count=vehicle_profile.row_count,
            parser_version=PARQUET_PARSER_VERSION,
        )
    ]

    schedule_archive_path: Path | None = None
    schedule_archive_sha256: str | None = None
    schedule_database_path: Path | None = None
    schedule_database_sha256: str | None = None
    schedule_schema_fingerprint: str | None = None
    derived_entries = []
    if include_schedule:
        schedule_profile = _load_yaml(schedule_profile_path, "schedule source profile")
        _require_acceptance_version(schedule_profile, "schedule source profile")
        schedule_url = _string(schedule_profile.get("url"), "schedule.url")
        response_profile = _mapping(
            schedule_profile.get("response_profile"), "schedule.response_profile"
        )
        expected_schedule_size = _integer(
            response_profile.get("content_length_bytes"), "schedule content length"
        )
        expected_schedule_etag = _string(response_profile.get("etag"), "schedule ETag")
        expected_schedule_modified = _utc(
            response_profile.get("last_modified_utc"), "schedule Last-Modified"
        )
        content_lock = _mapping(schedule_profile.get("content_lock", {}), "schedule.content_lock")
        expected_schedule_sha256 = _optional_sha256(
            content_lock.get("sha256"), "schedule.content_lock.sha256"
        )
        expected_database_sha256 = _optional_sha256(
            content_lock.get("expanded_sha256"), "schedule.content_lock.expanded_sha256"
        )
        expected_database_size = _optional_positive_integer(
            content_lock.get("expanded_size_bytes"),
            "schedule.content_lock.expanded_size_bytes",
        )
        expected_database_schema = _optional_sha256(
            content_lock.get("expanded_schema_fingerprint"),
            "schedule.content_lock.expanded_schema_fingerprint",
        )
        expected_expansion_version = content_lock.get("expansion_version")
        if (
            expected_expansion_version is not None
            and _string(
                expected_expansion_version,
                "schedule.content_lock.expansion_version",
            )
            != GZIP_EXPANSION_VERSION
        ):
            raise AcquisitionError("schedule expansion runtime does not match the content lock")
        schedule_archive_path = raw_root / "mbta-gtfs" / "2024" / "GTFS_ARCHIVE.db.gz"
        schedule_download = download_resumable(
            schedule_url,
            schedule_archive_path,
            allowed_hosts=frozenset({SCHEDULE_HOST}),
            maximum_bytes=expected_schedule_size,
            expected_size_bytes=expected_schedule_size,
            expected_sha256=expected_schedule_sha256,
            expected_etag=expected_schedule_etag,
            expected_last_modified_at_utc=expected_schedule_modified,
            timeout_seconds=120,
        )
        schedule_archive_sha256 = schedule_download.sha256
        format_fingerprint = hashlib.sha256(
            canonical_json_bytes({"compression": "gzip", "payload": "sqlite3"})
        ).hexdigest()
        content_entries.append(
            acquisition_content_entry(
                schedule_download,
                source_object_key=urlsplit(schedule_url).path.removeprefix("/"),
                source_url=schedule_url,
                schema_fingerprint=format_fingerprint,
                row_count=0,
                parser_version=SCHEDULE_PARSER_VERSION,
            )
        )
        schedule_database_path = raw_root / "mbta-gtfs" / "2024" / "GTFS_ARCHIVE.db"
        expanded_size, schedule_database_sha256 = expand_gzip_bounded(
            schedule_archive_path,
            schedule_database_path,
            maximum_output_bytes=MAX_SCHEDULE_DATABASE_BYTES,
        )
        if (
            expected_database_sha256 is not None
            and schedule_database_sha256 != expected_database_sha256
        ):
            raise AcquisitionError("expanded schedule SHA-256 does not match the content lock")
        if expected_database_size is not None and expanded_size != expected_database_size:
            raise AcquisitionError("expanded schedule size does not match the content lock")
        schedule_schema_fingerprint = sqlite_schema_fingerprint(schedule_database_path)
        if (
            expected_database_schema is not None
            and schedule_schema_fingerprint != expected_database_schema
        ):
            raise AcquisitionError("expanded schedule schema does not match the content lock")
        derived_entries.append(
            schedule_derived_entry(
                compressed_sha256=schedule_archive_sha256,
                expanded_path=schedule_database_path,
                expanded_sha256=schedule_database_sha256,
                schema_fingerprint=schedule_schema_fingerprint,
            )
        )

    lock_sha256 = write_acquisition_lock(
        acquisition_lock_path,
        content_entries=content_entries,
        derived_entries=derived_entries,
    )
    return PinnedSourceResult(
        vehicle_path=vehicle_path,
        vehicle_sha256=vehicle_download.sha256,
        vehicle_schema_fingerprint=vehicle_profile.schema_fingerprint,
        vehicle_row_count=vehicle_profile.row_count,
        schedule_archive_path=schedule_archive_path,
        schedule_archive_sha256=schedule_archive_sha256,
        schedule_database_path=schedule_database_path,
        schedule_database_sha256=schedule_database_sha256,
        schedule_schema_fingerprint=schedule_schema_fingerprint,
        acquisition_lock_path=acquisition_lock_path,
        acquisition_lock_sha256=lock_sha256,
    )


def result_payload(result: PinnedSourceResult) -> dict[str, object]:
    """Convert one acquisition result to deterministic CLI JSON."""

    payload = asdict(result)
    return {key: str(value) if isinstance(value, Path) else value for key, value in payload.items()}
