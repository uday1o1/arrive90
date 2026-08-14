from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest
import yaml
from arrive90_evaluation.travel_time_qualification import (
    PINNED_DATE,
    QualificationError,
    QualificationRun,
    qualify_day,
)
from arrive90_ingestion.acquisition import parquet_profile, sha256_file
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

ACCEPTANCE_VERSION = "travel-time-v1.1"
SOURCE_KEY = "feeds/mbta_all/day.parquet"
SOURCE_URL = f"https://busobservatory-lake.s3.amazonaws.com/{SOURCE_KEY}"


@dataclass(frozen=True, slots=True)
class QualificationFixture:
    raw_root: Path
    bus_profile: Path
    schedule_profile: Path
    acquisition_lock: Path
    acceptance_charter: Path


def _row(
    observed_at: datetime,
    sequence: int,
    stop_id: str,
    status: int,
) -> dict[str, object]:
    return {
        ENTITY_ID: f"entity-{observed_at.minute}-{status}",
        TRIP_ID: "trip-1",
        TRIP_START_TIME: "08:00:00",
        TRIP_START_DATE: "20240515",
        SCHEDULE_RELATIONSHIP: 0.0,
        ROUTE_ID: "Red",
        DIRECTION_ID: 0.0,
        LATITUDE: 42.35,
        LONGITUDE: -71.06,
        BEARING: 90.0,
        STOP_SEQUENCE: float(sequence),
        CURRENT_STATUS: float(status),
        OBSERVATION_TIMESTAMP: observed_at,
        STOP_ID: stop_id,
        VEHICLE_ID: "vehicle-1",
        VEHICLE_LABEL: "train-1",
        SPEED: 0.0,
    }


def _write_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE feed_info (
              feed_version TEXT, gtfs_active_date INTEGER, gtfs_end_date INTEGER
            );
            CREATE TABLE calendar (
              service_id TEXT, monday INTEGER, tuesday INTEGER, wednesday INTEGER,
              thursday INTEGER, friday INTEGER, saturday INTEGER, sunday INTEGER,
              start_date INTEGER, end_date INTEGER,
              gtfs_active_date INTEGER, gtfs_end_date INTEGER
            );
            CREATE TABLE calendar_dates (
              service_id TEXT, date INTEGER, exception_type INTEGER,
              gtfs_active_date INTEGER, gtfs_end_date INTEGER
            );
            CREATE TABLE route_patterns (
              route_pattern_id TEXT, route_id TEXT, direction_id INTEGER,
              gtfs_active_date INTEGER, gtfs_end_date INTEGER
            );
            CREATE TABLE trips (
              route_id TEXT, service_id TEXT, trip_id TEXT, direction_id INTEGER,
              route_pattern_id TEXT, gtfs_active_date INTEGER, gtfs_end_date INTEGER
            );
            CREATE TABLE stop_times (
              trip_id TEXT, arrival_time TEXT, departure_time TEXT, stop_id TEXT,
              stop_sequence INTEGER, gtfs_active_date INTEGER, gtfs_end_date INTEGER
            );
            INSERT INTO feed_info VALUES (
              'Spring, 2024-05-14T19:00:00+00:00, A', 20240515, 20240515
            );
            INSERT INTO calendar VALUES (
              'weekday', 1, 1, 1, 1, 1, 0, 0, 20240101, 20241231,
              20240101, 20241231
            );
            INSERT INTO route_patterns VALUES (
              'Red-1-0', 'Red', 0, 20240101, 20241231
            );
            INSERT INTO trips VALUES (
              'Red', 'weekday', 'trip-1', 0, 'Red-1-0', 20240101, 20241231
            );
            INSERT INTO stop_times VALUES (
              'trip-1', '08:00:00', '08:00:00', 'stop-1', 1, 20240101, 20241231
            );
            INSERT INTO stop_times VALUES (
              'trip-1', '08:05:00', '08:05:00', 'stop-10', 10, 20240101, 20241231
            );
            """
        )


def _write_yaml(path: Path, payload: object) -> None:
    path.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")


def _fixture(tmp_path: Path) -> QualificationFixture:
    raw_root = tmp_path / "raw"
    vehicle = raw_root / "bus-observatory" / "mbta_all" / "day.parquet"
    vehicle.parent.mkdir(parents=True)
    rows = [
        _row(datetime(2024, 5, 15, 12, 0), 1, "stop-1", 1),
        _row(datetime(2024, 5, 15, 12, 1), 10, "stop-10", 0),
        _row(datetime(2024, 5, 15, 12, 2), 10, "stop-10", 1),
    ]
    pq.write_table(pa.Table.from_pylist(rows), vehicle)
    vehicle_profile = parquet_profile(vehicle)

    schedule_root = raw_root / "mbta-gtfs" / "2024"
    schedule_root.mkdir(parents=True)
    schedule_archive = schedule_root / "GTFS_ARCHIVE.db.gz"
    schedule_archive.write_bytes(b"synthetic schedule archive control")
    schedule_database = schedule_root / "GTFS_ARCHIVE.db"
    _write_database(schedule_database)

    bus_profile = tmp_path / "bus.yaml"
    _write_yaml(
        bus_profile,
        {
            "acceptance_version": ACCEPTANCE_VERSION,
            "sample": {
                "inventory_date": PINNED_DATE,
                "row_count": len(rows),
                "schema_fingerprint": vehicle_profile.schema_fingerprint,
                "sha256": sha256_file(vehicle),
                "size_bytes": vehicle.stat().st_size,
                "url": SOURCE_URL,
                "whole_object_max_naive_utc": "2024-05-15 12:02:00",
                "whole_object_min_naive_utc": "2024-05-15 12:00:00",
            },
        },
    )
    schedule_profile = tmp_path / "schedule.yaml"
    _write_yaml(schedule_profile, {"acceptance_version": ACCEPTANCE_VERSION})
    acquisition_lock = tmp_path / "acquisition.json"
    acquisition_lock.write_text(
        json.dumps(
            {
                "acceptance_version": ACCEPTANCE_VERSION,
                "content_entries": [
                    {
                        "response_size_bytes": vehicle.stat().st_size,
                        "row_count": len(rows),
                        "schema_fingerprint": vehicle_profile.schema_fingerprint,
                        "sha256": sha256_file(vehicle),
                        "source_object_key": SOURCE_KEY,
                    },
                    {
                        "response_size_bytes": schedule_archive.stat().st_size,
                        "sha256": sha256_file(schedule_archive),
                        "source_object_key": "lamp/gtfs_archive/2024/GTFS_ARCHIVE.db.gz",
                    },
                ],
                "derived_entries": [
                    {
                        "output_sha256": sha256_file(schedule_database),
                        "output_size_bytes": schedule_database.stat().st_size,
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    acceptance_charter = tmp_path / "charter.yaml"
    _write_yaml(
        acceptance_charter,
        {
            "acceptance_version": ACCEPTANCE_VERSION,
            "one_day_gate": {
                "required_routes": ["Red"],
                "identity_availability_overall_min": 1.0,
                "identity_availability_per_line_min": 1.0,
                "trackable_multi_stop_episode_rate_per_line_min": 1.0,
                "finite_examples_per_line_min": 1,
                "finite_interval_width_coverage_per_line_min": 1.0,
            },
        },
    )
    return QualificationFixture(
        raw_root=raw_root,
        bus_profile=bus_profile,
        schedule_profile=schedule_profile,
        acquisition_lock=acquisition_lock,
        acceptance_charter=acceptance_charter,
    )


def _qualify(fixture: QualificationFixture, runtime_root: Path) -> QualificationRun:
    return qualify_day(
        PINNED_DATE,
        raw_root=fixture.raw_root,
        bus_profile_path=fixture.bus_profile,
        schedule_profile_path=fixture.schedule_profile,
        acquisition_lock_path=fixture.acquisition_lock,
        acceptance_charter_path=fixture.acceptance_charter,
        runtime_root=runtime_root,
    )


def test_qualification_is_deterministic_and_exercises_public_cli(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = _fixture(tmp_path)
    first = _qualify(fixture, tmp_path / "runtime-1")
    second = _qualify(fixture, tmp_path / "runtime-2")

    assert first.checks_passed
    assert second.checks_passed
    assert first.normalized_manifest_sha256 == second.normalized_manifest_sha256
    assert first.example_manifest_sha256 == second.example_manifest_sha256
    assert (
        first.normalized_manifest_path.read_bytes() == second.normalized_manifest_path.read_bytes()
    )
    assert first.example_manifest_path.read_bytes() == second.example_manifest_path.read_bytes()
    assert (
        main(
            [
                "data",
                "qualify-day",
                "--date",
                PINNED_DATE.isoformat(),
                "--raw-root",
                str(fixture.raw_root),
                "--bus-profile",
                str(fixture.bus_profile),
                "--schedule-profile",
                str(fixture.schedule_profile),
                "--acquisition-lock",
                str(fixture.acquisition_lock),
                "--acceptance-charter",
                str(fixture.acceptance_charter),
                "--runtime-root",
                str(tmp_path / "runtime-cli"),
            ]
        )
        == 0
    )
    cli_payload = json.loads(capsys.readouterr().out)
    assert cli_payload["checks_passed"] is True

    summary = json.loads(first.run_summary_path.read_text(encoding="utf-8"))
    assert summary["checks_passed"] is True
    assert all(check["passed"] for check in summary["checks"])
    examples = json.loads(first.example_manifest_path.read_text(encoding="utf-8"))
    assert examples["example_count"] == 1
    assert examples["feature_probe"]["source_observation_ids"]


def test_qualification_fails_closed_on_invalid_date_and_envelope(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    with pytest.raises(QualificationError, match="pinned"):
        qualify_day(
            date(2024, 5, 14),
            raw_root=fixture.raw_root,
            bus_profile_path=fixture.bus_profile,
            schedule_profile_path=fixture.schedule_profile,
            acquisition_lock_path=fixture.acquisition_lock,
            acceptance_charter_path=fixture.acceptance_charter,
            runtime_root=tmp_path / "wrong-date",
        )

    charter = yaml.safe_load(fixture.acceptance_charter.read_text(encoding="utf-8"))
    charter["acceptance_version"] = "legacy"
    _write_yaml(fixture.acceptance_charter, charter)
    with pytest.raises(QualificationError, match="stale acceptance version"):
        _qualify(fixture, tmp_path / "stale")


def test_qualification_rejects_conflicting_immutable_output(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    runtime = tmp_path / "runtime"
    first = _qualify(fixture, runtime)
    assert first.checks_passed
    first.normalized_manifest_path.write_text("{}", encoding="utf-8")
    with pytest.raises(QualificationError, match="conflicting bytes"):
        _qualify(fixture, runtime)
