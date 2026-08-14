from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest
from arrive90_data_contracts.source import AcquisitionContentEntry, DerivedArtifactEntry
from arrive90_ingestion.acquisition import (
    DownloadResult,
    acquisition_content_entry,
    parquet_profile,
    schedule_derived_entry,
    sha256_file,
    sqlite_schema_fingerprint,
    write_acquisition_lock,
)
from arrive90_ingestion.cli import main
from arrive90_ingestion.vehicle import (
    BEARING,
    CURRENT_STATUS,
    DIRECTION_ID,
    ENTITY_ID,
    LATITUDE,
    LONGITUDE,
    OBSERVATION_TIMESTAMP,
    ROUTE_ID,
    SCHEDULE_RELATIONSHIP,
    SPEED,
    STOP_ID,
    STOP_SEQUENCE,
    TRIP_ID,
    TRIP_START_DATE,
    TRIP_START_TIME,
    VEHICLE_ID,
    VEHICLE_LABEL,
)
from arrive90_ingestion.year_normalization import (
    YearNormalizationError,
    YearNormalizationResult,
    normalize_year,
    read_normalized_partition,
)

ACCEPTANCE_VERSION = "travel-time-v1.2"
ACQUIRED_AT = datetime(2026, 8, 14, 12, tzinfo=UTC)
INVENTORY_DATES = (date(2023, 12, 31), date(2024, 5, 15), date(2025, 1, 1))


def _row(
    observed_at: datetime,
    *,
    trip_start_date: str,
    trip_id: str,
    route_id: str = "Red",
    stop_sequence: int = 1,
    stop_id: str = "stop-1",
    latitude: float = 42.35,
    status: int = 1,
) -> dict[str, object]:
    return {
        ENTITY_ID: f"entity-{trip_id}",
        TRIP_ID: trip_id,
        TRIP_START_TIME: "08:00:00",
        TRIP_START_DATE: trip_start_date,
        SCHEDULE_RELATIONSHIP: 0.0,
        ROUTE_ID: route_id,
        DIRECTION_ID: 0.0,
        LATITUDE: latitude,
        LONGITUDE: -71.06,
        BEARING: 90.0,
        STOP_SEQUENCE: float(stop_sequence),
        CURRENT_STATUS: float(status),
        OBSERVATION_TIMESTAMP: observed_at,
        STOP_ID: stop_id,
        VEHICLE_ID: "vehicle-1",
        VEHICLE_LABEL: "train-1",
        SPEED: 0.0,
    }


def _schedule(raw_root: Path) -> tuple[AcquisitionContentEntry, DerivedArtifactEntry]:
    schedule_root = raw_root / "mbta-gtfs" / "2024"
    schedule_root.mkdir(parents=True)
    archive = schedule_root / "GTFS_ARCHIVE.db.gz"
    archive.write_bytes(b"schedule archive")
    database = schedule_root / "GTFS_ARCHIVE.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE feed_info "
            "(feed_version TEXT, gtfs_active_date INTEGER, gtfs_end_date INTEGER)"
        )
        connection.execute(
            "INSERT INTO feed_info VALUES "
            "('Full year, 2023-12-15T12:00:00+00:00, A', 20240101, 20241231)"
        )
    result = DownloadResult(
        path=archive,
        size_bytes=archive.stat().st_size,
        sha256=sha256_file(archive),
        etag="schedule-etag",
        last_modified_at_utc=ACQUIRED_AT,
        downloaded_at_utc=ACQUIRED_AT,
    )
    content = acquisition_content_entry(
        result,
        source_object_key="lamp/gtfs_archive/2024/GTFS_ARCHIVE.db.gz",
        source_url="https://performancedata.mbta.com/lamp/gtfs_archive/2024/GTFS_ARCHIVE.db.gz",
        schema_fingerprint="b" * 64,
        row_count=1,
        parser_version="schedule-test-v1",
    )
    derived = schedule_derived_entry(
        compressed_sha256=result.sha256,
        expanded_path=database,
        expanded_sha256=sha256_file(database),
        schema_fingerprint=sqlite_schema_fingerprint(database),
    )
    return content, derived


def _inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    raw_root = tmp_path / "raw"
    vehicle_root = raw_root / "bus-observatory" / "mbta_all"
    vehicle_root.mkdir(parents=True)
    rows_by_date = (
        [
            _row(
                datetime(2024, 1, 1, 12),
                trip_start_date="20240101",
                trip_id="trip-a",
            ),
            _row(
                datetime(2024, 1, 1, 12, 1),
                trip_start_date="20240101",
                trip_id="trip-conflict",
                stop_sequence=2,
                stop_id="stop-2",
            ),
        ],
        [
            _row(
                datetime(2024, 1, 1, 12),
                trip_start_date="20240101",
                trip_id="trip-a",
            ),
            _row(
                datetime(2024, 1, 1, 12),
                trip_start_date="20240101",
                trip_id="trip-a",
            ),
            _row(
                datetime(2024, 1, 1, 12, 1),
                trip_start_date="20240101",
                trip_id="trip-conflict",
                stop_sequence=2,
                stop_id="stop-2",
                latitude=43.0,
            ),
            _row(
                datetime(2024, 5, 15, 12),
                trip_start_date="20240515",
                trip_id="trip-orange",
                route_id="Orange",
            ),
            _row(
                datetime(2024, 5, 15, 12, 1),
                trip_start_date="20240515",
                trip_id="trip-invalid",
                route_id="Orange",
                status=99,
            ),
        ],
        [
            _row(
                datetime(2025, 1, 1, 1),
                trip_start_date="20241231",
                trip_id="trip-boundary",
            )
        ],
    )
    inventory_entries = []
    content_entries = []
    for inventory_date, rows in zip(INVENTORY_DATES, rows_by_date, strict=True):
        filename = f"COMPACTED_mbta_all_{inventory_date.isoformat()}_13:42:00.parquet"
        path = vehicle_root / filename
        pq.write_table(pa.Table.from_pylist(rows), path)
        key = f"feeds/mbta_all/{filename}"
        url = f"https://busobservatory-lake.s3.amazonaws.com/{key}"
        inventory_entries.append(
            {
                "declared_size_mb": 0.01,
                "inventory_date": inventory_date.isoformat(),
                "inventory_generated_at": "2026-08-14T05:00:51+00:00",
                "inventory_snapshot_sha256": "a" * 64,
                "inventory_snapshot_url": (
                    "https://busobservatory-lake.s3.amazonaws.com/index/data-inventory.json"
                ),
                "source_object_key": key,
                "source_url": url,
            }
        )
        profile = parquet_profile(path)
        download = DownloadResult(
            path=path,
            size_bytes=path.stat().st_size,
            sha256=sha256_file(path),
            etag=f"etag-{inventory_date}",
            last_modified_at_utc=ACQUIRED_AT,
            downloaded_at_utc=ACQUIRED_AT,
        )
        content_entries.append(
            acquisition_content_entry(
                download,
                source_object_key=key,
                source_url=url,
                schema_fingerprint=profile.schema_fingerprint,
                row_count=profile.row_count,
                parser_version="test-v1",
            )
        )
    inventory = tmp_path / "inventory.json"
    inventory.write_text(
        json.dumps({"acceptance_version": ACCEPTANCE_VERSION, "entries": inventory_entries}),
        encoding="utf-8",
    )
    schedule_content, schedule_derived = _schedule(raw_root)
    acquisition = tmp_path / "acquisition.json"
    write_acquisition_lock(
        acquisition,
        content_entries=(*content_entries, schedule_content),
        derived_entries=(schedule_derived,),
    )
    return raw_root, inventory, acquisition


def _patch_count(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("arrive90_ingestion.year_acquisition.EXPECTED_OBJECT_COUNT", 3)
    monkeypatch.setattr("arrive90_ingestion.year_normalization.EXPECTED_OBJECT_COUNT", 3)


def test_complete_year_normalization_is_partitioned_deterministic_and_lineaged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_count(monkeypatch)
    raw_root, inventory, acquisition = _inputs(tmp_path)
    first = normalize_year(
        2024,
        inventory_lock_path=inventory,
        acquisition_lock_path=acquisition,
        raw_root=raw_root,
        normalized_root=tmp_path / "normalized-1",
        runtime_root=tmp_path / "runtime-1",
    )
    second = normalize_year(
        2024,
        inventory_lock_path=inventory,
        acquisition_lock_path=acquisition,
        raw_root=raw_root,
        normalized_root=tmp_path / "normalized-2",
        runtime_root=tmp_path / "runtime-2",
    )

    assert first.dataset_manifest_sha256 == second.dataset_manifest_sha256
    assert first.dataset_manifest_path.read_bytes() == second.dataset_manifest_path.read_bytes()
    assert first.observation_count == 3
    assert first.partition_count == 3
    assert first.quarantine_count == 3
    manifest = json.loads(first.dataset_manifest_path.read_text(encoding="utf-8"))
    assert manifest["summary"]["source_object_count"] == 3
    assert manifest["summary"]["exact_duplicate_row_count"] == 2
    assert manifest["summary"]["overlap_conflicting_identity_count"] == 1
    assert manifest["quarantine"]["reason_counts"] == {
        "CONFLICTING_OVERLAP_STATE": 2,
        "INVALID_SOURCE_ROW": 1,
    }
    observations = tuple(
        observation
        for partition in manifest["partitions"]
        for observation in read_normalized_partition(
            (tmp_path / "normalized-1") / partition["path"]
        )
    )
    assert len({observation.observation_id for observation in observations}) == 3
    duplicate = next(observation for observation in observations if observation.trip_id == "trip-a")
    assert len(duplicate.source_lineage) == 3
    assert all(observation.trip_id != "trip-conflict" for observation in observations)
    schedule = json.loads(first.schedule_index_path.read_text(encoding="utf-8"))
    assert len(schedule["schedule_days"]) == 366
    runtime = json.loads(first.runtime_report_path.read_text(encoding="utf-8"))
    assert runtime["maximum_concurrent_source_objects"] == 1
    assert runtime["peak_resident_memory_bytes"] > 0


def test_normalization_rejects_tampered_acquired_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_count(monkeypatch)
    raw_root, inventory, acquisition = _inputs(tmp_path)
    source = next((raw_root / "bus-observatory" / "mbta_all").glob("*.parquet"))
    source.write_bytes(b"tampered")
    with pytest.raises(YearNormalizationError, match="content verification"):
        normalize_year(
            2024,
            inventory_lock_path=inventory,
            acquisition_lock_path=acquisition,
            raw_root=raw_root,
            normalized_root=tmp_path / "normalized",
            runtime_root=tmp_path / "runtime",
        )


@pytest.mark.parametrize("drift", ["missing_vehicle", "missing_derived", "row_count"])
def test_normalization_rejects_acquisition_lock_drift(
    drift: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_count(monkeypatch)
    raw_root, inventory, acquisition = _inputs(tmp_path)
    payload = json.loads(acquisition.read_text(encoding="utf-8"))
    if drift == "missing_vehicle":
        payload["content_entries"] = payload["content_entries"][1:]
        message = "complete inventory"
    elif drift == "missing_derived":
        payload["derived_entries"] = []
        message = "one schedule"
    else:
        vehicle = next(
            entry
            for entry in payload["content_entries"]
            if entry["source_object_key"].endswith(".parquet")
        )
        vehicle["row_count"] += 1
        message = "content verification"
    acquisition.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(YearNormalizationError, match=message):
        normalize_year(
            2024,
            inventory_lock_path=inventory,
            acquisition_lock_path=acquisition,
            raw_root=raw_root,
            normalized_root=tmp_path / "normalized",
            runtime_root=tmp_path / "runtime",
        )


def test_normalization_rejects_unknown_locked_schema_and_missing_schedule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_count(monkeypatch)
    raw_root, inventory, acquisition = _inputs(tmp_path)
    source = next((raw_root / "bus-observatory" / "mbta_all").glob("*.parquet"))
    rows = pq.ParquetFile(source).read().to_pylist()
    for row in rows:
        row["producer.unknown"] = "value"
    pq.write_table(pa.Table.from_pylist(rows), source)
    payload = json.loads(acquisition.read_text(encoding="utf-8"))
    entry = next(
        item
        for item in payload["content_entries"]
        if item["source_object_key"].endswith(source.name)
    )
    profile = parquet_profile(source)
    entry.update(
        response_size_bytes=source.stat().st_size,
        row_count=profile.row_count,
        schema_fingerprint=profile.schema_fingerprint,
        sha256=sha256_file(source),
    )
    acquisition.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(YearNormalizationError, match="unknown acquired schema"):
        normalize_year(
            2024,
            inventory_lock_path=inventory,
            acquisition_lock_path=acquisition,
            raw_root=raw_root,
            normalized_root=tmp_path / "normalized",
            runtime_root=tmp_path / "runtime",
        )

    raw_root, inventory, acquisition = _inputs(tmp_path / "missing-schedule")
    (raw_root / "mbta-gtfs" / "2024" / "GTFS_ARCHIVE.db").unlink()
    with pytest.raises(YearNormalizationError, match="schedule database"):
        normalize_year(
            2024,
            inventory_lock_path=inventory,
            acquisition_lock_path=acquisition,
            raw_root=raw_root,
            normalized_root=tmp_path / "normalized-missing-schedule",
            runtime_root=tmp_path / "runtime-missing-schedule",
        )


@pytest.mark.parametrize("target", ["staging", "partition"])
def test_normalization_refuses_conflicting_immutable_parquet(
    target: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_count(monkeypatch)
    raw_root, inventory, acquisition = _inputs(tmp_path)
    normalized_root = tmp_path / "normalized"
    result = normalize_year(
        2024,
        inventory_lock_path=inventory,
        acquisition_lock_path=acquisition,
        raw_root=raw_root,
        normalized_root=normalized_root,
        runtime_root=tmp_path / "runtime",
    )
    if target == "staging":
        corrupt = next((normalized_root / "staging").rglob("*.parquet"))
    else:
        manifest = json.loads(result.dataset_manifest_path.read_text(encoding="utf-8"))
        corrupt = normalized_root / manifest["partitions"][0]["path"]
    corrupt.write_bytes(b"corrupt")
    with pytest.raises(YearNormalizationError, match=r"different bytes|corrupt"):
        normalize_year(
            2024,
            inventory_lock_path=inventory,
            acquisition_lock_path=acquisition,
            raw_root=raw_root,
            normalized_root=normalized_root,
            runtime_root=tmp_path / "runtime-2",
        )


def test_public_cli_dispatches_year_normalization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = YearNormalizationResult(
        dataset_manifest_path=tmp_path / "manifest.json",
        dataset_manifest_sha256="a" * 64,
        runtime_report_path=tmp_path / "runtime.json",
        partition_count=10,
        observation_count=100,
        quarantine_count=2,
        schedule_index_path=tmp_path / "schedule.json",
        schedule_index_sha256="b" * 64,
    )
    monkeypatch.setattr("arrive90_ingestion.cli.normalize_year", lambda *_args, **_kwargs: result)
    assert main(["data", "normalize", "--year", "2024"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dataset_manifest_sha256"] == "a" * 64
    assert payload["observation_count"] == 100
