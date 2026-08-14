from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urlsplit

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest
import yaml
from arrive90_ingestion.acquisition import AcquisitionError, DownloadResult, sha256_file
from arrive90_ingestion.cli import main
from arrive90_ingestion.pinned_sources import acquire_pinned_day

PINNED_DATE = date(2024, 5, 15)
VEHICLE_URL = (
    "https://busobservatory-lake.s3.amazonaws.com/feeds/mbta_all/"
    "COMPACTED_mbta_all_2024-05-15_13:42:26.parquet"
)
SCHEDULE_URL = "https://performancedata.mbta.com/lamp/gtfs_archive/2024/GTFS_ARCHIVE.db.gz"
ACQUIRED_AT = datetime(2026, 8, 14, 12, tzinfo=UTC)


def _source_files(tmp_path: Path) -> tuple[Path, Path]:
    vehicle = tmp_path / "source.parquet"
    pq.write_table(pa.table({"vehicle.trip.trip_id": ["trip-1", "trip-2"]}), vehicle)
    database = tmp_path / "source.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE feed_info "
            "(feed_version TEXT, gtfs_active_date INTEGER, gtfs_end_date INTEGER)"
        )
        connection.execute(
            "INSERT INTO feed_info VALUES "
            "('MBTA, 2024-05-10T12:00:00+00:00, v1', 20240501, 20240531)"
        )
    schedule = tmp_path / "source.db.gz"
    with gzip.open(schedule, "wb") as stream:
        stream.write(database.read_bytes())
    return vehicle, schedule


def _config_files(
    tmp_path: Path,
    *,
    vehicle: Path,
    schedule: Path,
) -> tuple[Path, Path, Path]:
    inventory = tmp_path / "inventory-lock.json"
    inventory.write_text(
        json.dumps(
            {
                "acceptance_version": "travel-time-v1.2",
                "entries": [
                    {
                        "inventory_date": PINNED_DATE.isoformat(),
                        "source_object_key": urlsplit(VEHICLE_URL).path.removeprefix("/"),
                        "source_url": VEHICLE_URL,
                        "declared_size_mb": vehicle.stat().st_size / 1024 / 1024,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    bus_profile = tmp_path / "bus.yaml"
    bus_profile.write_text(
        yaml.safe_dump(
            {
                "acceptance_version": "travel-time-v1.2",
                "sample": {
                    "inventory_date": PINNED_DATE,
                    "url": VEHICLE_URL,
                    "size_bytes": vehicle.stat().st_size,
                    "sha256": sha256_file(vehicle),
                    "row_count": 2,
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    schedule_profile = tmp_path / "schedule.yaml"
    schedule_profile.write_text(
        yaml.safe_dump(
            {
                "acceptance_version": "travel-time-v1.2",
                "url": SCHEDULE_URL,
                "response_profile": {
                    "content_length_bytes": schedule.stat().st_size,
                    "etag": "etag-1",
                    "last_modified_utc": "2025-01-01T12:04:22Z",
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return inventory, bus_profile, schedule_profile


def test_public_cli_acquires_profiles_and_locks_pinned_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    vehicle, schedule = _source_files(tmp_path)
    inventory, bus_profile, schedule_profile = _config_files(
        tmp_path,
        vehicle=vehicle,
        schedule=schedule,
    )

    def fake_download(url: str, destination: Path, **_kwargs: object) -> DownloadResult:
        source = vehicle if url == VEHICLE_URL else schedule
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
        return DownloadResult(
            path=destination,
            size_bytes=source.stat().st_size,
            sha256=sha256_file(source),
            etag=None if url == VEHICLE_URL else "etag-1",
            last_modified_at_utc=(
                None if url == VEHICLE_URL else datetime(2025, 1, 1, 12, 4, 22, tzinfo=UTC)
            ),
            downloaded_at_utc=ACQUIRED_AT,
        )

    monkeypatch.setattr("arrive90_ingestion.pinned_sources.download_resumable", fake_download)
    raw_root = tmp_path / "raw"
    acquisition_lock = tmp_path / "acquired.json"
    assert (
        main(
            [
                "source",
                "download",
                "--date",
                PINNED_DATE.isoformat(),
                "--include-schedule",
                "--inventory-lock",
                str(inventory),
                "--bus-profile",
                str(bus_profile),
                "--schedule-profile",
                str(schedule_profile),
                "--raw-root",
                str(raw_root),
                "--acquisition-lock",
                str(acquisition_lock),
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    assert Path(output["vehicle_path"]).is_file()
    assert Path(output["schedule_database_path"]).is_file()
    assert output["vehicle_row_count"] == 2
    assert len(output["schedule_database_sha256"]) == 64
    lock_payload = json.loads(acquisition_lock.read_text(encoding="utf-8"))
    assert len(lock_payload["content_entries"]) == 2
    assert len(lock_payload["derived_entries"]) == 1


def test_pinned_day_rejects_other_dates_before_network(tmp_path: Path) -> None:
    vehicle, schedule = _source_files(tmp_path)
    inventory, bus_profile, schedule_profile = _config_files(
        tmp_path,
        vehicle=vehicle,
        schedule=schedule,
    )
    loaded = json.loads(inventory.read_text(encoding="utf-8"))
    loaded["entries"][0]["inventory_date"] = "2024-05-14"
    inventory.write_text(json.dumps(loaded), encoding="utf-8")
    with pytest.raises(AcquisitionError, match="frozen to the pinned sample"):
        acquire_pinned_day(
            date(2024, 5, 14),
            include_schedule=False,
            inventory_lock_path=inventory,
            bus_profile_path=bus_profile,
            schedule_profile_path=schedule_profile,
            raw_root=tmp_path / "raw",
            acquisition_lock_path=tmp_path / "acquired.json",
        )


def test_pinned_day_fails_closed_on_wrong_acceptance_version(tmp_path: Path) -> None:
    inventory = tmp_path / "inventory.json"
    inventory.write_text(
        json.dumps({"acceptance_version": "legacy", "entries": []}), encoding="utf-8"
    )
    with pytest.raises(AcquisitionError, match=r"travel-time-v1\.2"):
        acquire_pinned_day(
            PINNED_DATE,
            include_schedule=False,
            inventory_lock_path=inventory,
            bus_profile_path=tmp_path / "unused.yaml",
            raw_root=tmp_path / "raw",
            acquisition_lock_path=tmp_path / "acquired.json",
        )


def test_pinned_day_rejects_source_profile_acceptance_drift(tmp_path: Path) -> None:
    vehicle, schedule = _source_files(tmp_path)
    inventory, bus_profile, schedule_profile = _config_files(
        tmp_path,
        vehicle=vehicle,
        schedule=schedule,
    )
    profile = yaml.safe_load(bus_profile.read_text(encoding="utf-8"))
    profile["acceptance_version"] = "legacy"
    bus_profile.write_text(yaml.safe_dump(profile), encoding="utf-8")
    with pytest.raises(AcquisitionError, match="source profile acceptance version"):
        acquire_pinned_day(
            PINNED_DATE,
            include_schedule=False,
            inventory_lock_path=inventory,
            bus_profile_path=bus_profile,
            schedule_profile_path=schedule_profile,
            raw_root=tmp_path / "raw",
            acquisition_lock_path=tmp_path / "acquired.json",
        )


def test_pinned_day_rejects_schedule_profile_acceptance_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vehicle, schedule = _source_files(tmp_path)
    inventory, bus_profile, schedule_profile = _config_files(
        tmp_path,
        vehicle=vehicle,
        schedule=schedule,
    )
    profile = yaml.safe_load(schedule_profile.read_text(encoding="utf-8"))
    profile["acceptance_version"] = "legacy"
    schedule_profile.write_text(yaml.safe_dump(profile), encoding="utf-8")

    def fake_download(url: str, destination: Path, **_kwargs: object) -> DownloadResult:
        assert url == VEHICLE_URL
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(vehicle.read_bytes())
        return DownloadResult(
            path=destination,
            size_bytes=vehicle.stat().st_size,
            sha256=sha256_file(vehicle),
            etag=None,
            last_modified_at_utc=None,
            downloaded_at_utc=ACQUIRED_AT,
        )

    monkeypatch.setattr("arrive90_ingestion.pinned_sources.download_resumable", fake_download)
    with pytest.raises(AcquisitionError, match="schedule source profile acceptance version"):
        acquire_pinned_day(
            PINNED_DATE,
            include_schedule=True,
            inventory_lock_path=inventory,
            bus_profile_path=bus_profile,
            schedule_profile_path=schedule_profile,
            raw_root=tmp_path / "raw",
            acquisition_lock_path=tmp_path / "acquired.json",
        )


def test_pinned_day_rejects_sample_url_drift(tmp_path: Path) -> None:
    vehicle, schedule = _source_files(tmp_path)
    inventory, bus_profile, schedule_profile = _config_files(
        tmp_path,
        vehicle=vehicle,
        schedule=schedule,
    )
    profile = yaml.safe_load(bus_profile.read_text(encoding="utf-8"))
    profile["sample"]["url"] = f"{VEHICLE_URL}.changed"
    bus_profile.write_text(yaml.safe_dump(profile), encoding="utf-8")
    with pytest.raises(AcquisitionError, match="canonical inventory"):
        acquire_pinned_day(
            PINNED_DATE,
            include_schedule=False,
            inventory_lock_path=inventory,
            bus_profile_path=bus_profile,
            schedule_profile_path=schedule_profile,
            raw_root=tmp_path / "raw",
            acquisition_lock_path=tmp_path / "acquired.json",
        )


def test_pinned_day_rejects_invalid_optional_content_hash(tmp_path: Path) -> None:
    vehicle, schedule = _source_files(tmp_path)
    inventory, bus_profile, schedule_profile = _config_files(
        tmp_path,
        vehicle=vehicle,
        schedule=schedule,
    )
    profile = yaml.safe_load(schedule_profile.read_text(encoding="utf-8"))
    profile["content_lock"] = {"sha256": "bad"}
    schedule_profile.write_text(yaml.safe_dump(profile), encoding="utf-8")

    def fake_vehicle_download(_url: str, destination: Path, **_kwargs: object) -> DownloadResult:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(vehicle.read_bytes())
        return DownloadResult(
            path=destination,
            size_bytes=vehicle.stat().st_size,
            sha256=hashlib.sha256(vehicle.read_bytes()).hexdigest(),
            etag=None,
            last_modified_at_utc=None,
            downloaded_at_utc=ACQUIRED_AT,
        )

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "arrive90_ingestion.pinned_sources.download_resumable", fake_vehicle_download
        )
        with pytest.raises(AcquisitionError, match="lowercase hexadecimal"):
            acquire_pinned_day(
                PINNED_DATE,
                include_schedule=True,
                inventory_lock_path=inventory,
                bus_profile_path=bus_profile,
                schedule_profile_path=schedule_profile,
                raw_root=tmp_path / "raw",
                acquisition_lock_path=tmp_path / "acquired.json",
            )
