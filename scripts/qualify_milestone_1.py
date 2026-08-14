"""Run and freeze the complete travel-time-v1.1 Milestone 1 gate."""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
import subprocess
import sys
from collections import Counter
from datetime import date
from itertools import pairwise
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from arrive90_ingestion.vehicle import normalize_vehicle_parquet

ROOT = Path(__file__).resolve().parents[1]
ACCEPTANCE_VERSION = "travel-time-v1.1"
YEAR = 2024
EXPECTED_OBJECT_COUNT = 368
EXPECTED_PARTITION_COUNT = 1_098
EXPECTED_SERVICE_DAY_COUNT = 366
EXPECTED_ROUTES = frozenset({"Blue", "Orange", "Red"})

ACCEPTANCE_PATH = ROOT / "configs/acceptance/travel-time-v1.1.yaml"
ACQUISITION_LOCK_PATH = ROOT / "configs/source-locks/mbta-2024-acquired.json"
INVENTORY_LOCK_PATH = ROOT / "configs/source-locks/mbta-2024.json"
NORMALIZED_ROOT = ROOT / "data/normalized"
RAW_ROOT = ROOT / "data/raw"
FIRST_RUNTIME_PATH = ROOT / "artifacts/runtime/milestone-1/normalization-run.json"
RESTART_RUNTIME_PATH = ROOT / "artifacts/runtime/milestone-1-restart/normalization-run.json"
QUALIFICATION_PATH = ROOT / "artifacts/reports/qualification/milestone-1-normalization-v1.1.json"
GATE_PATH = ROOT / "artifacts/reports/gates/milestone-1.json"

ARRIVE90 = shutil.which("arrive90") or ""
GIT = shutil.which("git") or ""
MAKE = shutil.which("make") or ""
if not ARRIVE90 or not GIT or not MAKE:
    raise RuntimeError("arrive90, git, and make must be available on PATH")


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return loaded


def _git(*args: str) -> str:
    process = subprocess.run(  # noqa: S603
        [GIT, *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return process.stdout.strip()


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _combined_digest(paths: tuple[Path, ...]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.as_posix().encode()):
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _run_acquisition_verification() -> tuple[int, dict[str, Any] | None]:
    process = subprocess.run(  # noqa: S603
        [ARRIVE90, "source", "download", "--year", str(YEAR), "--workers", "4"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if process.returncode != 0:
        return process.returncode, None
    payload = json.loads(process.stdout)
    if not isinstance(payload, dict):
        raise ValueError("acquisition verification must emit a JSON object")
    return process.returncode, payload


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a nonempty string")
    return value


def _manifest_path(runtime: dict[str, Any]) -> Path:
    relative = _text(runtime.get("dataset_manifest_path"), "dataset_manifest_path")
    path = NORMALIZED_ROOT / relative
    if not path.is_file():
        raise ValueError(f"dataset manifest does not exist: {path}")
    return path


def _all_true(value: pa.BooleanArray | pa.ChunkedArray) -> bool:
    result = pc.all(value)
    return bool(result.as_py())


def _validate_partitions(manifest: dict[str, Any]) -> dict[str, Any]:
    raw_partitions = manifest.get("partitions")
    if not isinstance(raw_partitions, list):
        raise ValueError("dataset manifest partitions must be a list")
    expected_keys = {
        (route, date(YEAR, 1, 1).fromordinal(ordinal).isoformat())
        for route in EXPECTED_ROUTES
        for ordinal in range(
            date(YEAR, 1, 1).toordinal(),
            date(YEAR, 12, 31).toordinal() + 1,
        )
    }
    observed_keys: set[tuple[str, str]] = set()
    normalized_rows = 0
    lineage_entries = 0
    merged_observation_count = 0
    partition_bytes = 0
    timestamp_min: int | None = None
    timestamp_max: int | None = None
    invalid_partitions: list[str] = []

    for index, raw_partition in enumerate(raw_partitions):
        if not isinstance(raw_partition, dict):
            raise ValueError(f"partition {index} must be an object")
        route = _text(raw_partition.get("route_id"), f"partition {index} route_id")
        service_date = _text(raw_partition.get("service_date"), f"partition {index} service_date")
        relative = _text(raw_partition.get("path"), f"partition {index} path")
        expected_rows = _integer(raw_partition.get("row_count"), f"partition {index} rows")
        expected_sha256 = _text(raw_partition.get("sha256"), f"partition {index} sha256")
        key = (route, service_date)
        if key in observed_keys:
            invalid_partitions.append(f"duplicate partition key {route}/{service_date}")
        observed_keys.add(key)
        path = NORMALIZED_ROOT / relative
        expected_prefix = f"vehicle/year={YEAR}/route_id={route}/service_date={service_date}/"
        if not path.is_file() or not relative.startswith(expected_prefix):
            invalid_partitions.append(f"missing or misplaced partition {relative}")
            continue
        partition_bytes += path.stat().st_size
        if _digest(path) != expected_sha256:
            invalid_partitions.append(f"content hash mismatch {relative}")
            continue
        parquet = pq.ParquetFile(path)
        if parquet.metadata.num_rows != expected_rows:
            invalid_partitions.append(f"row count mismatch {relative}")
            continue
        table = parquet.read(
            columns=[
                "observation_id",
                "source_lineage",
                "route_id",
                "trip_start_date",
                "observation_source_naive_utc",
                "observation_utc",
            ]
        )
        normalized_rows += table.num_rows
        if table["observation_id"].null_count:
            invalid_partitions.append(f"null observation identifier {relative}")
        elif pc.count_distinct(table["observation_id"]).as_py() != table.num_rows:
            invalid_partitions.append(f"duplicate observation identifier {relative}")
        lineage_lengths = pc.list_value_length(table["source_lineage"])
        minimum_lineage = pc.min(lineage_lengths).as_py() if table.num_rows else None
        if minimum_lineage is None or int(minimum_lineage) < 1:
            invalid_partitions.append(f"empty source lineage {relative}")
        else:
            lineage_entries += int(pc.sum(lineage_lengths).as_py())
            merged_observation_count += int(
                pc.sum(pc.cast(pc.greater(lineage_lengths, 1), pa.int64())).as_py()
            )
        if not _all_true(pc.equal(table["route_id"], pa.scalar(route))):
            invalid_partitions.append(f"route key mismatch {relative}")
        service_scalar = pa.scalar(date.fromisoformat(service_date), type=pa.date32())
        if not _all_true(pc.equal(table["trip_start_date"], service_scalar)):
            invalid_partitions.append(f"service-date key mismatch {relative}")
        source_time = pc.cast(table["observation_source_naive_utc"], pa.int64())
        utc_time = pc.cast(table["observation_utc"], pa.int64())
        if not _all_true(pc.equal(source_time, utc_time)):
            invalid_partitions.append(f"source UTC attachment changed clock time {relative}")
        if table.num_rows:
            local_min = int(pc.min(utc_time).as_py())
            local_max = int(pc.max(utc_time).as_py())
            timestamp_min = local_min if timestamp_min is None else min(timestamp_min, local_min)
            timestamp_max = local_max if timestamp_max is None else max(timestamp_max, local_max)

    return {
        "all_partition_checks_passed": not invalid_partitions,
        "expected_partition_keys_complete": observed_keys == expected_keys,
        "invalid_partitions": invalid_partitions,
        "lineage_entry_count": lineage_entries,
        "merged_observation_count": merged_observation_count,
        "normalized_observation_count": normalized_rows,
        "partition_bytes": partition_bytes,
        "partition_count": len(raw_partitions),
        "timestamp_max_epoch_microseconds": timestamp_max,
        "timestamp_min_epoch_microseconds": timestamp_min,
    }


def _validate_quarantine(manifest: dict[str, Any]) -> dict[str, Any]:
    raw = manifest.get("quarantine")
    if not isinstance(raw, dict):
        raise ValueError("dataset manifest quarantine must be an object")
    relative = _text(raw.get("path"), "quarantine path")
    path = NORMALIZED_ROOT / relative
    expected_rows = _integer(raw.get("row_count"), "quarantine row count")
    expected_sha256 = _text(raw.get("sha256"), "quarantine sha256")
    if not path.is_file():
        raise ValueError(f"quarantine artifact does not exist: {path}")
    table = pq.read_table(path)
    reasons = Counter(str(value) for value in table["reason"].to_pylist())
    expected_reasons = raw.get("reason_counts")
    return {
        "bytes": path.stat().st_size,
        "content_hash_matches": _digest(path) == expected_sha256,
        "reason_counts": dict(sorted(reasons.items())),
        "reason_counts_match": reasons == Counter(expected_reasons),
        "row_count": table.num_rows,
        "row_count_matches": table.num_rows == expected_rows,
        "only_frozen_reasons": set(reasons)
        == {"CONFLICTING_DUPLICATE_STATE", "CONFLICTING_OVERLAP_STATE"},
    }


def _validate_schedule(manifest: dict[str, Any], acquisition: dict[str, Any]) -> dict[str, Any]:
    raw = manifest.get("schedule_index")
    if not isinstance(raw, dict):
        raise ValueError("dataset manifest schedule index must be an object")
    relative = _text(raw.get("path"), "schedule index path")
    path = NORMALIZED_ROOT / relative
    expected_sha256 = _text(raw.get("sha256"), "schedule index sha256")
    schedule = _load_json(path)
    raw_days = schedule.get("schedule_days")
    if not isinstance(raw_days, list):
        raise ValueError("schedule index days must be a list")
    observed_dates = [
        _text(day.get("service_date"), "schedule service date")
        for day in raw_days
        if isinstance(day, dict)
    ]
    return {
        "bytes": path.stat().st_size,
        "content_hash_matches": _digest(path) == expected_sha256,
        "database_hash_matches_acquisition": schedule.get("database_sha256")
        == acquisition.get("schedule_database_sha256"),
        "first_service_date": observed_dates[0] if observed_dates else None,
        "last_service_date": observed_dates[-1] if observed_dates else None,
        "service_dates_are_unique_and_ordered": observed_dates == sorted(set(observed_dates)),
        "service_day_count": len(raw_days),
    }


def _inventory_gap_report(manifest: dict[str, Any]) -> dict[str, Any]:
    raw_sources = manifest.get("source_objects")
    if not isinstance(raw_sources, list):
        raise ValueError("dataset manifest source objects must be a list")
    dates = [
        date.fromisoformat(_text(source.get("inventory_date"), "source inventory date"))
        for source in raw_sources
        if isinstance(source, dict)
    ]
    gaps = [(current - previous).days for previous, current in pairwise(dates)]
    return {
        "first_inventory_date": dates[0].isoformat() if dates else None,
        "last_inventory_date": dates[-1].isoformat() if dates else None,
        "maximum_inventory_date_gap_days": max(gaps, default=0),
        "missing_inventory_date_count": sum(max(0, gap - 1) for gap in gaps),
        "source_object_count": len(dates),
    }


def _validate_boundary_exclusions(manifest: dict[str, Any]) -> dict[str, Any]:
    raw_sources = manifest.get("source_objects")
    if not isinstance(raw_sources, list):
        raise ValueError("dataset manifest source objects must be a list")
    boundary_sources: list[dict[str, Any]] = []
    for raw_source in raw_sources:
        if not isinstance(raw_source, dict):
            continue
        minimum = _text(raw_source.get("source_min_naive_utc"), "source minimum timestamp")
        maximum = _text(raw_source.get("source_max_naive_utc"), "source maximum timestamp")
        if (
            date.fromisoformat(minimum[:10]).year != YEAR
            or date.fromisoformat(maximum[:10]).year != YEAR
        ):
            boundary_sources.append(raw_source)

    excluded_lineage_count = 0
    excluded_observation_count = 0
    source_keys: list[str] = []
    for raw_source in boundary_sources:
        source_key = _text(raw_source.get("source_object_key"), "boundary source key")
        path = RAW_ROOT / "bus-observatory/mbta_all" / Path(source_key).name
        normalized = normalize_vehicle_parquet(path, source_object_key=source_key)
        excluded = [
            observation
            for observation in normalized.observations
            if observation.trip_start_date.year != YEAR
        ]
        excluded_observation_count += len(excluded)
        excluded_lineage_count += sum(len(observation.source_lineage) for observation in excluded)
        source_keys.append(source_key)
    return {
        "boundary_source_count": len(boundary_sources),
        "boundary_source_keys": source_keys,
        "excluded_lineage_count": excluded_lineage_count,
        "excluded_observation_count": excluded_observation_count,
    }


def build_reports() -> tuple[dict[str, Any], dict[str, Any]]:
    check_process = subprocess.run(  # noqa: S603
        [MAKE, "check"], cwd=ROOT, check=False
    )
    acquisition_code, acquisition = _run_acquisition_verification()
    first_runtime = _load_json(FIRST_RUNTIME_PATH)
    restart_runtime = _load_json(RESTART_RUNTIME_PATH)
    manifest_path = _manifest_path(first_runtime)
    manifest_sha256 = _digest(manifest_path)
    manifest = _load_json(manifest_path)
    acquisition_lock = _load_json(ACQUISITION_LOCK_PATH)
    partition_report = _validate_partitions(manifest)
    quarantine_report = _validate_quarantine(manifest)
    if acquisition is None:
        acquisition = {}
    schedule_report = _validate_schedule(manifest, acquisition)
    gap_report = _inventory_gap_report(manifest)
    boundary_report = _validate_boundary_exclusions(manifest)

    raw_ignored = (
        subprocess.run(  # noqa: S603
            [GIT, "check-ignore", "-q", "data/raw"], cwd=ROOT, check=False
        ).returncode
        == 0
    )
    normalized_ignored = (
        subprocess.run(  # noqa: S603
            [GIT, "check-ignore", "-q", "data/normalized"], cwd=ROOT, check=False
        ).returncode
        == 0
    )
    bulk_status = _git("status", "--porcelain", "--", "data/raw", "data/normalized")

    raw_content = acquisition_lock.get("content_entries")
    derived_entries = acquisition_lock.get("derived_entries")
    if not isinstance(raw_content, list) or not isinstance(derived_entries, list):
        raise ValueError("acquisition lock entries must be lists")
    vehicle_entries = [
        entry
        for entry in raw_content
        if isinstance(entry, dict) and str(entry.get("source_object_key", "")).endswith(".parquet")
    ]
    schedule_entries = [
        entry
        for entry in raw_content
        if isinstance(entry, dict) and str(entry.get("source_object_key", "")).endswith(".db.gz")
    ]
    schema_counts = Counter(
        _text(entry.get("schema_fingerprint"), "source schema fingerprint")
        for entry in vehicle_entries
    )
    manifest_registry = manifest.get("schema_registry")
    if not isinstance(manifest_registry, dict):
        raise ValueError("dataset manifest schema registry must be an object")
    manifest_schema_counts = {
        str(fingerprint): _integer(value.get("object_count"), "schema object count")
        for fingerprint, value in manifest_registry.items()
        if isinstance(value, dict)
    }
    summary = manifest.get("summary")
    invariants = manifest.get("invariants")
    if not isinstance(summary, dict) or not isinstance(invariants, dict):
        raise ValueError("dataset manifest summary and invariants must be objects")
    expected_lineage_entries = _integer(
        summary.get("retained_raw_row_count"), "retained raw rows"
    ) - _integer(summary.get("quarantined_row_count"), "quarantined rows")

    checks = {
        "all_368_vehicle_objects_and_schedule_verified": (
            acquisition_code == 0
            and acquisition.get("object_count") == EXPECTED_OBJECT_COUNT
            and len(vehicle_entries) == EXPECTED_OBJECT_COUNT
            and len(schedule_entries) == 1
            and len(derived_entries) == 1
        ),
        "all_partition_content_and_contract_checks_passed": bool(
            partition_report["all_partition_checks_passed"]
        ),
        "canonical_observation_identifiers_unique": bool(
            invariants.get("canonical_observation_ids_unique")
            and partition_report["all_partition_checks_passed"]
        ),
        "complete_route_day_partition_coverage": (
            partition_report["partition_count"] == EXPECTED_PARTITION_COUNT
            and partition_report["expected_partition_keys_complete"]
        ),
        "conflicting_states_are_quarantined": (
            quarantine_report["content_hash_matches"]
            and quarantine_report["row_count_matches"]
            and quarantine_report["reason_counts_match"]
            and quarantine_report["only_frozen_reasons"]
            and _integer(summary.get("overlap_conflicting_identity_count"), "overlap conflicts") > 0
        ),
        "every_retained_row_is_lineaged_or_quarantined": (
            partition_report["lineage_entry_count"] + boundary_report["excluded_lineage_count"]
            == expected_lineage_entries
            and partition_report["merged_observation_count"] > 0
            and bool(invariants.get("complete_source_lineage"))
        ),
        "fresh_process_manifest_is_byte_identical": (
            first_runtime.get("dataset_manifest_sha256") == manifest_sha256
            and restart_runtime.get("dataset_manifest_sha256") == manifest_sha256
            and _manifest_path(restart_runtime).read_bytes() == manifest_path.read_bytes()
        ),
        "full_year_gap_report_has_no_missing_inventory_date": (
            gap_report["source_object_count"] == EXPECTED_OBJECT_COUNT
            and gap_report["maximum_inventory_date_gap_days"] == 1
            and gap_report["missing_inventory_date_count"] == 0
        ),
        "known_schema_registry_covers_every_object": (
            len(schema_counts) == len(manifest_schema_counts)
            and dict(schema_counts) == manifest_schema_counts
            and sum(schema_counts.values()) == EXPECTED_OBJECT_COUNT
        ),
        "make_check_passed": check_process.returncode == 0,
        "normalized_counts_match_manifest": (
            partition_report["normalized_observation_count"]
            == _integer(summary.get("normalized_observation_count"), "normalized rows")
            and partition_report["partition_count"]
            == _integer(summary.get("partition_count"), "partition count")
        ),
        "processing_is_file_bounded_and_memory_reported": (
            first_runtime.get("maximum_concurrent_source_objects") == 1
            and restart_runtime.get("maximum_concurrent_source_objects") == 1
            and _integer(first_runtime.get("peak_resident_memory_bytes"), "peak memory") > 0
            and _integer(restart_runtime.get("peak_resident_memory_bytes"), "restart peak memory")
            > 0
        ),
        "raw_and_normalized_bulk_data_are_ignored": (
            raw_ignored and normalized_ignored and not bulk_status
        ),
        "schedule_index_is_complete_and_verified": (
            schedule_report["content_hash_matches"]
            and schedule_report["database_hash_matches_acquisition"]
            and schedule_report["service_day_count"] == EXPECTED_SERVICE_DAY_COUNT
            and schedule_report["first_service_date"] == "2024-01-01"
            and schedule_report["last_service_date"] == "2024-12-31"
            and schedule_report["service_dates_are_unique_and_ordered"]
        ),
        "source_utc_attachment_precedes_deduplication": (
            bool(invariants.get("source_utc_attached_before_deduplication"))
            and partition_report["all_partition_checks_passed"]
        ),
    }
    failing = sorted(name for name, passed in checks.items() if not passed)
    implementation_paths = (
        ROOT / "packages/ingestion/src/arrive90_ingestion/year_acquisition.py",
        ROOT / "packages/ingestion/src/arrive90_ingestion/year_normalization.py",
        ROOT / "packages/ingestion/src/arrive90_ingestion/vehicle.py",
        ROOT / "packages/ingestion/src/arrive90_ingestion/cli.py",
        ROOT / "packages/ingestion/tests/test_year_acquisition.py",
        ROOT / "packages/ingestion/tests/test_year_normalization.py",
        ROOT / "packages/ingestion/tests/test_vehicle.py",
        ROOT / "scripts/qualify_milestone_1.py",
    )
    input_hashes = {
        "acceptance_charter": _digest(ACCEPTANCE_PATH),
        "acquisition_lock": _digest(ACQUISITION_LOCK_PATH),
        "build_plan": _digest(ROOT / "BUILD_PLAN.md"),
        "dataset_manifest": manifest_sha256,
        "implementation": _combined_digest(implementation_paths),
        "inventory_lock": _digest(INVENTORY_LOCK_PATH),
        "schedule_index": _text(manifest["schedule_index"].get("sha256"), "schedule hash"),
        "uv_lock": _digest(ROOT / "uv.lock"),
    }
    environment = {
        "implementation_commit": _git("rev-parse", "HEAD"),
        "machine": platform.machine(),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
    }
    observed = {
        "acquisition": acquisition,
        "boundary_exclusion_report": boundary_report,
        "gap_report": gap_report,
        "normalization_first_process": first_runtime,
        "normalization_restart_process": restart_runtime,
        "partition_report": partition_report,
        "quarantine_report": quarantine_report,
        "schedule_report": schedule_report,
        "schema_object_counts": dict(sorted(schema_counts.items())),
        "summary": summary,
    }
    qualification = {
        "acceptance_version": ACCEPTANCE_VERSION,
        "checks": checks,
        "environment": environment,
        "failing_checks": failing,
        "input_manifest_hashes": input_hashes,
        "observed": observed,
        "qualification_command": "make qualify-milestone1",
        "state": "PASSED" if not failing else "FAILED",
        "year": YEAR,
    }
    gate = {
        "acceptance_charter_sha256": input_hashes["acceptance_charter"],
        "acceptance_version": ACCEPTANCE_VERSION,
        "checks": checks,
        "command": "make qualify-milestone1 && make gate MILESTONE=1",
        "environment": environment,
        "failing_checks": failing,
        "input_manifest_hashes": input_hashes,
        "milestone": 1,
        "observed": observed,
        "qualification_report_sha256": "PENDING",
        "state": "ACCEPTED" if not failing else "IN_PROGRESS",
    }
    return qualification, gate


def main() -> int:
    qualification, gate = build_reports()
    _write(QUALIFICATION_PATH, qualification)
    gate["qualification_report_sha256"] = _digest(QUALIFICATION_PATH)
    _write(GATE_PATH, gate)
    print(QUALIFICATION_PATH.relative_to(ROOT))
    print(GATE_PATH.relative_to(ROOT))
    return 0 if gate["state"] == "ACCEPTED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
