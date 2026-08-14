"""File-bounded complete-year normalization and content-addressed partition writing."""

from __future__ import annotations

import hashlib
import json
import os
import resource
import tempfile
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime, timedelta
from datetime import time as datetime_time
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from arrive90_data_contracts.gates import DEFAULT_ACCEPTANCE_VERSION
from arrive90_data_contracts.travel_time import (
    HistoricalVehicleStatus,
    SourceLineageEntry,
    TripScheduleRelationship,
    VehicleObservation,
)

from arrive90_ingestion.acquisition import (
    parquet_profile,
    select_schedule_version,
    sha256_file,
    sqlite_schema_fingerprint,
)
from arrive90_ingestion.historical import canonical_json_bytes
from arrive90_ingestion.inventory import EXPECTED_OBJECT_COUNT
from arrive90_ingestion.pinned_sources import DEFAULT_RAW_ROOT
from arrive90_ingestion.vehicle import (
    RAIL_ROUTE_IDS,
    QuarantinedVehicleRow,
    VehicleNormalizationError,
    normalize_vehicle_parquet,
    validate_vehicle_schema,
)
from arrive90_ingestion.year_acquisition import (
    DEFAULT_FULL_ACQUISITION_LOCK,
    DEFAULT_INVENTORY_LOCK,
    load_acquisition_lock,
    load_inventory_entries,
)

NORMALIZATION_VERSION = "travel-time-normalization-v1"
DEFAULT_NORMALIZED_ROOT = Path("data/normalized")
DEFAULT_RUNTIME_ROOT = Path("artifacts/runtime/milestone-1")
PARQUET_OPTIONS: dict[str, object] = {
    "compression": "zstd",
    "compression_level": 9,
    "data_page_version": "2.0",
    "use_dictionary": True,
    "version": "2.6",
    "write_statistics": True,
}

LINEAGE_TYPE = pa.list_(
    pa.struct(
        [
            pa.field("source_object_key", pa.string(), nullable=False),
            pa.field("source_row_ordinal", pa.int64(), nullable=False),
        ]
    )
)
OBSERVATION_SCHEMA = pa.schema(
    [
        pa.field("observation_id", pa.string(), nullable=False),
        pa.field("source_lineage", LINEAGE_TYPE, nullable=False),
        pa.field("entity_id", pa.string()),
        pa.field("trip_id", pa.string(), nullable=False),
        pa.field("trip_start_date", pa.date32(), nullable=False),
        pa.field("trip_start_time", pa.string(), nullable=False),
        pa.field("schedule_relationship", pa.string(), nullable=False),
        pa.field("route_id", pa.string(), nullable=False),
        pa.field("direction_id", pa.int8(), nullable=False),
        pa.field("vehicle_id", pa.string(), nullable=False),
        pa.field("vehicle_label", pa.string()),
        pa.field("observation_source_naive_utc", pa.timestamp("us"), nullable=False),
        pa.field("observation_utc", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("stop_sequence", pa.int32()),
        pa.field("stop_id", pa.string()),
        pa.field("current_status", pa.string(), nullable=False),
        pa.field("latitude", pa.float64()),
        pa.field("longitude", pa.float64()),
        pa.field("bearing", pa.float64()),
        pa.field("speed", pa.float64()),
        pa.field("schema_version", pa.string(), nullable=False),
    ]
)
QUARANTINE_SCHEMA = pa.schema(
    [
        pa.field("source_object_key", pa.string(), nullable=False),
        pa.field("source_row_ordinal", pa.int64(), nullable=False),
        pa.field("reason", pa.string(), nullable=False),
        pa.field("detail", pa.string(), nullable=False),
    ]
)


class YearNormalizationError(ValueError):
    """Raised when complete-year normalization cannot preserve its frozen invariants."""


@dataclass(frozen=True, slots=True)
class YearNormalizationResult:
    """Public paths and hashes emitted by complete-year normalization."""

    dataset_manifest_path: Path
    dataset_manifest_sha256: str
    runtime_report_path: Path
    partition_count: int
    observation_count: int
    quarantine_count: int
    schedule_index_path: Path
    schedule_index_sha256: str


def _observation_sort_key(observation: VehicleObservation) -> tuple[object, ...]:
    return (
        observation.observation_utc,
        observation.trip_start_time.encode(),
        observation.trip_id.encode(),
        observation.direction_id,
        observation.vehicle_id.encode(),
        observation.stop_sequence is None,
        observation.stop_sequence if observation.stop_sequence is not None else 0,
        observation.current_status.value.encode(),
        observation.observation_id,
    )


def _observation_row(observation: VehicleObservation) -> dict[str, object]:
    return {
        "bearing": observation.bearing,
        "current_status": observation.current_status.value,
        "direction_id": observation.direction_id,
        "entity_id": observation.entity_id,
        "latitude": observation.latitude,
        "longitude": observation.longitude,
        "observation_id": observation.observation_id,
        "observation_source_naive_utc": observation.observation_source_naive_utc,
        "observation_utc": observation.observation_utc,
        "route_id": observation.route_id,
        "schedule_relationship": observation.schedule_relationship.value,
        "schema_version": observation.schema_version,
        "source_lineage": [asdict(entry) for entry in observation.source_lineage],
        "speed": observation.speed,
        "stop_id": observation.stop_id,
        "stop_sequence": observation.stop_sequence,
        "trip_id": observation.trip_id,
        "trip_start_date": observation.trip_start_date,
        "trip_start_time": observation.trip_start_time,
        "vehicle_id": observation.vehicle_id,
        "vehicle_label": observation.vehicle_label,
    }


def _observations_table(observations: tuple[VehicleObservation, ...]) -> pa.Table:
    return pa.Table.from_pylist(
        [_observation_row(observation) for observation in observations],
        schema=OBSERVATION_SCHEMA,
    )


def _mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise YearNormalizationError(f"{field} must be an object with string keys")
    return value


def _required[T](value: object, expected: type[T], field: str) -> T:
    if not isinstance(value, expected):
        raise YearNormalizationError(f"{field} has an invalid type")
    return value


def _required_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise YearNormalizationError(f"{field} has an invalid type")
    return float(value)


def _row_observation(value: object) -> VehicleObservation:
    row = _mapping(value, "normalized observation")
    raw_lineage = row.get("source_lineage")
    if not isinstance(raw_lineage, list):
        raise YearNormalizationError("source_lineage must be a list")
    lineage = tuple(
        SourceLineageEntry(
            source_object_key=_required(
                _mapping(item, "source lineage").get("source_object_key"),
                str,
                "source_object_key",
            ),
            source_row_ordinal=_required(
                _mapping(item, "source lineage").get("source_row_ordinal"),
                int,
                "source_row_ordinal",
            ),
        )
        for item in raw_lineage
    )
    try:
        return VehicleObservation(
            observation_id=_required(row.get("observation_id"), str, "observation_id"),
            source_lineage=lineage,
            entity_id=(
                None
                if row.get("entity_id") is None
                else _required(row.get("entity_id"), str, "entity_id")
            ),
            trip_id=_required(row.get("trip_id"), str, "trip_id"),
            trip_start_date=_required(row.get("trip_start_date"), date, "trip_start_date"),
            trip_start_time=_required(row.get("trip_start_time"), str, "trip_start_time"),
            schedule_relationship=TripScheduleRelationship(
                _required(row.get("schedule_relationship"), str, "schedule_relationship")
            ),
            route_id=_required(row.get("route_id"), str, "route_id"),
            direction_id=_required(row.get("direction_id"), int, "direction_id"),
            vehicle_id=_required(row.get("vehicle_id"), str, "vehicle_id"),
            vehicle_label=(
                None
                if row.get("vehicle_label") is None
                else _required(row.get("vehicle_label"), str, "vehicle_label")
            ),
            observation_source_naive_utc=_required(
                row.get("observation_source_naive_utc"),
                datetime,
                "observation_source_naive_utc",
            ),
            observation_utc=_required(row.get("observation_utc"), datetime, "observation_utc"),
            stop_sequence=(
                None
                if row.get("stop_sequence") is None
                else _required(row.get("stop_sequence"), int, "stop_sequence")
            ),
            stop_id=(
                None
                if row.get("stop_id") is None
                else _required(row.get("stop_id"), str, "stop_id")
            ),
            current_status=HistoricalVehicleStatus(
                _required(row.get("current_status"), str, "current_status")
            ),
            latitude=(
                None
                if row.get("latitude") is None
                else _required_number(row.get("latitude"), "latitude")
            ),
            longitude=(
                None
                if row.get("longitude") is None
                else _required_number(row.get("longitude"), "longitude")
            ),
            bearing=(
                None
                if row.get("bearing") is None
                else _required_number(row.get("bearing"), "bearing")
            ),
            speed=(
                None if row.get("speed") is None else _required_number(row.get("speed"), "speed")
            ),
            schema_version=_required(row.get("schema_version"), str, "schema_version"),
        )
    except ValueError as error:
        raise YearNormalizationError(f"invalid normalized observation: {error}") from error


def read_normalized_partition(path: Path) -> tuple[VehicleObservation, ...]:
    """Read one repository-owned normalized partition through the frozen contract."""

    table = pq.ParquetFile(path).read()
    if table.schema != OBSERVATION_SCHEMA:
        table = table.cast(OBSERVATION_SCHEMA)
    return tuple(_row_observation(row) for row in table.to_pylist())


def _write_parquet(path: Path, table: pa.Table) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix="arrive90-parquet-", dir=path.parent)
    os.close(descriptor)
    temporary = Path(name)
    try:
        pq.write_table(table, temporary, **PARQUET_OPTIONS)
        digest = sha256_file(temporary)
        if path.exists():
            if sha256_file(path) != digest:
                raise YearNormalizationError(
                    f"immutable Parquet output has different bytes: {path}"
                )
        else:
            os.replace(temporary, path)
        return digest
    finally:
        temporary.unlink(missing_ok=True)


def _write_content_addressed_parquet(
    directory: Path,
    table: pa.Table,
    prefix: str,
) -> tuple[Path, str]:
    directory.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix="arrive90-content-", dir=directory)
    os.close(descriptor)
    temporary = Path(name)
    try:
        pq.write_table(table, temporary, **PARQUET_OPTIONS)
        digest = sha256_file(temporary)
        output = directory / f"{prefix}-{digest}.parquet"
        if output.exists():
            if sha256_file(output) != digest:
                raise YearNormalizationError(f"content-addressed output is corrupt: {output}")
        else:
            os.replace(temporary, output)
        return output, digest
    finally:
        temporary.unlink(missing_ok=True)


def _write_content_addressed_json(
    directory: Path,
    payload: object,
    prefix: str,
) -> tuple[Path, str]:
    body = canonical_json_bytes(payload)
    digest = hashlib.sha256(body).hexdigest()
    output = directory / f"{prefix}-{digest}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        if output.read_bytes() != body:
            raise YearNormalizationError(f"content-addressed JSON output is corrupt: {output}")
    else:
        output.write_bytes(body)
    return output, digest


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _peak_resident_bytes() -> int:
    maximum = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return maximum if os.uname().sysname == "Darwin" else maximum * 1024


def _schedule_index(
    year: int,
    *,
    database: Path,
    database_sha256: str,
    normalized_root: Path,
) -> tuple[Path, str]:
    records: list[dict[str, object]] = []
    service_date = date(year, 1, 1)
    while service_date.year == year:
        cutoff = datetime.combine(
            service_date + timedelta(days=1),
            datetime_time(),
            tzinfo=UTC,
        )
        version = select_schedule_version(
            database,
            service_date=service_date,
            cutoff_utc=cutoff,
            expanded_database_sha256=database_sha256,
        )
        records.append({"service_date": service_date, **asdict(version)})
        service_date += timedelta(days=1)
    payload = {
        "acceptance_version": DEFAULT_ACCEPTANCE_VERSION,
        "database_schema_fingerprint": sqlite_schema_fingerprint(database),
        "database_sha256": database_sha256,
        "schedule_days": records,
        "schedule_index_version": NORMALIZATION_VERSION,
        "year": year,
    }
    return _write_content_addressed_json(
        normalized_root / "schedule" / str(year), payload, "schedule-index"
    )


def _partition_observations(
    paths: list[Path],
    *,
    route_id: str,
    service_date: date,
) -> tuple[tuple[VehicleObservation, ...], int, int, list[QuarantinedVehicleRow]]:
    observations = [
        observation for path in paths for observation in read_normalized_partition(path)
    ]
    by_identity: dict[str, list[VehicleObservation]] = defaultdict(list)
    for observation in observations:
        if observation.route_id != route_id or observation.trip_start_date != service_date:
            raise YearNormalizationError("fragment observation is in the wrong partition")
        by_identity[observation.observation_id].append(observation)
    canonical: list[VehicleObservation] = []
    exact_duplicate_rows = 0
    conflicting_identities = 0
    quarantined: list[QuarantinedVehicleRow] = []
    for observation_id in sorted(by_identity):
        matches = by_identity[observation_id]
        payloads = {observation.canonical_state_payload for observation in matches}
        if len(payloads) != 1:
            conflicting_identities += 1
            quarantined.extend(
                QuarantinedVehicleRow(
                    source_object_key=lineage.source_object_key,
                    source_row_ordinal=lineage.source_row_ordinal,
                    reason="CONFLICTING_OVERLAP_STATE",
                    detail=f"canonical identity {observation_id} has cross-object state conflicts",
                )
                for observation in matches
                for lineage in observation.source_lineage
            )
            continue
        lineage = tuple(
            sorted(
                {entry for observation in matches for entry in observation.source_lineage},
                key=lambda entry: (entry.source_object_key.encode(), entry.source_row_ordinal),
            )
        )
        exact_duplicate_rows += len(lineage) - 1
        canonical.append(replace(matches[0], source_lineage=lineage))
    canonical.sort(key=_observation_sort_key)
    quarantined.sort(
        key=lambda row: (
            row.source_object_key.encode(),
            row.source_row_ordinal,
            row.reason.encode(),
        )
    )
    if len({observation.observation_id for observation in canonical}) != len(canonical):
        raise YearNormalizationError("canonical observation identifiers are not unique")
    return tuple(canonical), exact_duplicate_rows, conflicting_identities, quarantined


def normalize_year(
    year: int,
    *,
    inventory_lock_path: Path = DEFAULT_INVENTORY_LOCK,
    acquisition_lock_path: Path = DEFAULT_FULL_ACQUISITION_LOCK,
    raw_root: Path = DEFAULT_RAW_ROOT,
    normalized_root: Path = DEFAULT_NORMALIZED_ROOT,
    runtime_root: Path = DEFAULT_RUNTIME_ROOT,
) -> YearNormalizationResult:
    """Normalize all frozen sources sequentially and write deterministic year partitions."""

    started = time.monotonic()
    inventory = load_inventory_entries(inventory_lock_path, year)
    content_entries, derived_entries = load_acquisition_lock(
        acquisition_lock_path, "full-year acquisition lock"
    )
    acquired_by_key = {
        entry.source_object_key: entry
        for entry in content_entries
        if entry.source_object_key.endswith(".parquet")
    }
    if len(acquired_by_key) != EXPECTED_OBJECT_COUNT or set(acquired_by_key) != {
        entry.source_object_key for entry in inventory
    }:
        raise YearNormalizationError("acquired vehicle objects do not match the complete inventory")
    if len(derived_entries) != 1:
        raise YearNormalizationError("full-year acquisition lock must contain one schedule index")
    acquisition_lock_sha256 = sha256_file(acquisition_lock_path)
    staging_root = normalized_root / "staging" / NORMALIZATION_VERSION / acquisition_lock_sha256
    fragments: dict[tuple[str, date], list[Path]] = defaultdict(list)
    source_summaries: list[dict[str, object]] = []
    schema_registry: dict[str, dict[str, object]] = {}
    quarantined: list[QuarantinedVehicleRow] = []
    total_source_rows = 0
    total_retained_rows = 0
    total_identity_complete = 0
    retained_by_route: Counter[str] = Counter()
    complete_by_route: Counter[str] = Counter()
    largest_source_bytes = 0

    for inventory_entry in inventory:
        acquired = acquired_by_key[inventory_entry.source_object_key]
        path = (
            raw_root / "bus-observatory" / "mbta_all" / Path(inventory_entry.source_object_key).name
        )
        if not path.is_file():
            raise YearNormalizationError(f"acquired source is missing: {path}")
        if (
            path.stat().st_size != acquired.response_size_bytes
            or sha256_file(path) != acquired.sha256
        ):
            raise YearNormalizationError(
                f"acquired source failed content verification: {inventory_entry.source_object_key}"
            )
        profile = parquet_profile(path)
        if (
            profile.row_count != acquired.row_count
            or profile.schema_fingerprint != acquired.schema_fingerprint
        ):
            raise YearNormalizationError(
                f"acquired source failed content verification: {inventory_entry.source_object_key}"
            )
        schema = pq.ParquetFile(path).schema_arrow
        try:
            schema_contract = validate_vehicle_schema(schema)
        except VehicleNormalizationError as error:
            raise YearNormalizationError(
                f"unknown acquired schema {profile.schema_fingerprint}: {error}"
            ) from error
        registry = schema_registry.setdefault(
            profile.schema_fingerprint,
            {
                "columns": schema_contract.columns,
                "object_count": 0,
                "present_optional_columns": schema_contract.present_optional_columns,
            },
        )
        registry_count = registry["object_count"]
        if isinstance(registry_count, bool) or not isinstance(registry_count, int):
            raise YearNormalizationError("schema registry object count is invalid")
        registry["object_count"] = registry_count + 1
        normalized = normalize_vehicle_parquet(
            path, source_object_key=inventory_entry.source_object_key
        )
        total_source_rows += normalized.source_row_count
        total_retained_rows += normalized.retained_raw_row_count
        total_identity_complete += normalized.identity_complete_row_count
        retained_by_route.update(dict(normalized.retained_rows_by_route))
        complete_by_route.update(dict(normalized.identity_complete_rows_by_route))
        largest_source_bytes = max(largest_source_bytes, path.stat().st_size)
        quarantined.extend(normalized.quarantined_rows)
        source_summaries.append(
            {
                "conflicting_identity_count": normalized.conflicting_identity_count,
                "exact_duplicate_row_count": normalized.exact_duplicate_row_count,
                "identity_availability_by_route": normalized.identity_availability_by_route,
                "identity_availability_overall": normalized.identity_availability_overall,
                "inventory_date": inventory_entry.inventory_date,
                "observation_count": len(normalized.observations),
                "quarantined_row_count": len(normalized.quarantined_rows),
                "retained_raw_row_count": normalized.retained_raw_row_count,
                "schema_fingerprint": normalized.source_schema_fingerprint,
                "source_max_naive_utc": normalized.source_max_naive_utc,
                "source_min_naive_utc": normalized.source_min_naive_utc,
                "source_object_key": inventory_entry.source_object_key,
                "source_row_count": normalized.source_row_count,
                "source_sha256": acquired.sha256,
            }
        )
        grouped: dict[tuple[str, date], list[VehicleObservation]] = defaultdict(list)
        for observation in normalized.observations:
            if observation.trip_start_date.year == year:
                grouped[(observation.route_id, observation.trip_start_date)].append(observation)
        for (route_id, service_date), values in grouped.items():
            values.sort(key=_observation_sort_key)
            fragment = (
                staging_root
                / f"route_id={route_id}"
                / f"service_date={service_date.isoformat()}"
                / f"{acquired.sha256}.parquet"
            )
            _write_parquet(fragment, _observations_table(tuple(values)))
            fragments[(route_id, service_date)].append(fragment)

    partitions: list[dict[str, object]] = []
    final_observation_count = 0
    exact_duplicate_rows = 0
    overlap_conflicts = 0
    for route_id, service_date in sorted(fragments, key=lambda item: (item[0].encode(), item[1])):
        observations, duplicate_count, conflict_count, overlap_quarantine = _partition_observations(
            fragments[(route_id, service_date)],
            route_id=route_id,
            service_date=service_date,
        )
        exact_duplicate_rows += duplicate_count
        overlap_conflicts += conflict_count
        quarantined.extend(overlap_quarantine)
        table = _observations_table(observations)
        partition_path, partition_sha256 = _write_content_addressed_parquet(
            normalized_root
            / "vehicle"
            / f"year={year}"
            / f"route_id={route_id}"
            / f"service_date={service_date.isoformat()}",
            table,
            "part",
        )
        final_observation_count += len(observations)
        partitions.append(
            {
                "path": _relative(partition_path, normalized_root),
                "route_id": route_id,
                "row_count": len(observations),
                "service_date": service_date,
                "sha256": partition_sha256,
                "source_fragment_count": len(fragments[(route_id, service_date)]),
            }
        )

    quarantined.sort(
        key=lambda row: (
            row.source_object_key.encode(),
            row.source_row_ordinal,
            row.reason.encode(),
        )
    )
    quarantine_table = pa.Table.from_pylist(
        [asdict(row) for row in quarantined], schema=QUARANTINE_SCHEMA
    )
    quarantine_path, quarantine_sha256 = _write_content_addressed_parquet(
        normalized_root / "quarantine" / f"year={year}",
        quarantine_table,
        "quarantine",
    )
    schedule_database = raw_root / "mbta-gtfs/2024/GTFS_ARCHIVE.db"
    schedule_derived = derived_entries[0]
    if (
        not schedule_database.is_file()
        or sha256_file(schedule_database) != schedule_derived.output_sha256
        or sqlite_schema_fingerprint(schedule_database) != schedule_derived.schema_fingerprint
    ):
        raise YearNormalizationError(
            "expanded schedule database failed derived-artifact verification"
        )
    schedule_index_path, schedule_index_sha256 = _schedule_index(
        year,
        database=schedule_database,
        database_sha256=schedule_derived.output_sha256,
        normalized_root=normalized_root,
    )
    manifest = {
        "acceptance_version": DEFAULT_ACCEPTANCE_VERSION,
        "acquisition_lock_sha256": acquisition_lock_sha256,
        "invariants": {
            "canonical_observation_ids_unique": True,
            "complete_source_lineage": True,
            "source_utc_attached_before_deduplication": True,
        },
        "normalization_version": NORMALIZATION_VERSION,
        "partitions": partitions,
        "quarantine": {
            "path": _relative(quarantine_path, normalized_root),
            "reason_counts": dict(Counter(row.reason for row in quarantined)),
            "row_count": len(quarantined),
            "sha256": quarantine_sha256,
        },
        "schedule_index": {
            "path": _relative(schedule_index_path, normalized_root),
            "sha256": schedule_index_sha256,
        },
        "schema_registry": schema_registry,
        "source_objects": source_summaries,
        "summary": {
            "exact_duplicate_row_count": exact_duplicate_rows,
            "identity_availability_by_route": {
                route_id: (
                    complete_by_route[route_id] / retained_by_route[route_id]
                    if retained_by_route[route_id]
                    else 0.0
                )
                for route_id in RAIL_ROUTE_IDS
            },
            "identity_availability_overall": (
                total_identity_complete / total_retained_rows if total_retained_rows else 0.0
            ),
            "normalized_observation_count": final_observation_count,
            "overlap_conflicting_identity_count": overlap_conflicts,
            "partition_count": len(partitions),
            "quarantined_row_count": len(quarantined),
            "retained_raw_row_count": total_retained_rows,
            "source_object_count": len(inventory),
            "source_row_count": total_source_rows,
        },
        "year": year,
    }
    manifest_path, manifest_sha256 = _write_content_addressed_json(
        normalized_root / "manifests" / str(year), manifest, "dataset-manifest"
    )
    elapsed = time.monotonic() - started
    runtime_report = {
        "acceptance_version": DEFAULT_ACCEPTANCE_VERSION,
        "dataset_manifest_path": _relative(manifest_path, normalized_root),
        "dataset_manifest_sha256": manifest_sha256,
        "elapsed_seconds": elapsed,
        "largest_source_object_bytes": largest_source_bytes,
        "maximum_concurrent_source_objects": 1,
        "normalization_version": NORMALIZATION_VERSION,
        "peak_resident_memory_bytes": _peak_resident_bytes(),
        "retained_rows_per_second": total_retained_rows / elapsed,
        "source_rows_per_second": total_source_rows / elapsed,
        "year": year,
    }
    runtime_root.mkdir(parents=True, exist_ok=True)
    runtime_path = runtime_root / "normalization-run.json"
    runtime_path.write_text(
        json.dumps(runtime_report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return YearNormalizationResult(
        dataset_manifest_path=manifest_path,
        dataset_manifest_sha256=manifest_sha256,
        runtime_report_path=runtime_path,
        partition_count=len(partitions),
        observation_count=final_observation_count,
        quarantine_count=len(quarantined),
        schedule_index_path=schedule_index_path,
        schedule_index_sha256=schedule_index_sha256,
    )
