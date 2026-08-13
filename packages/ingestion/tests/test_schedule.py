from __future__ import annotations

import json
import zipfile
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from arrive90_ingestion.historical import HistoricalObjectStore
from arrive90_ingestion.schedule import (
    NormalizedSchedule,
    main,
    normalize_schedule_archive,
    write_normalized_schedule,
)

NOW = datetime(2025, 1, 2, tzinfo=UTC)
SERVICE_DATE = date(2025, 1, 1)


def _archive(path: Path, *, stop_times_override: str | None = None) -> Path:
    files = {
        "agency.txt": (
            "agency_id,agency_name,agency_url,agency_timezone\n"
            "mbta,MBTA,https://www.mbta.com,America/New_York\n"
        ),
        "feed_info.txt": (
            "feed_publisher_name,feed_publisher_url,feed_lang,feed_start_date,"
            "feed_end_date,feed_version\nMBTA,https://www.mbta.com,en,20250101,20250131,v1\n"
        ),
        "stops.txt": (
            "stop_id,stop_name,parent_station,stop_lat,stop_lon,location_type\n"
            "place-a,Station A,,42.3600,-71.0600,1\n"
            "stop-a,Platform A,place-a,42.3600,-71.0600,0\n"
            "stop-b,Platform B,,42.3650,-71.0550,0\n"
        ),
        "routes.txt": (
            "route_id,agency_id,route_short_name,route_long_name,route_type\n"
            "Red,mbta,Red,Red Line,1\n"
        ),
        "trips.txt": (
            "route_id,service_id,trip_id,direction_id,block_id,wheelchair_accessible\n"
            "Red,weekday,trip-b,1,block-b,1\n"
            "Red,removed,trip-removed,0,,0\n"
            "Red,weekday,trip-a,0,block-a,2\n"
        ),
        "stop_times.txt": (
            "trip_id,arrival_time,departure_time,stop_id,stop_sequence,pickup_type,drop_off_type\n"
            "trip-a,24:00:00,24:00:10,stop-a,1,0,0\n"
            "trip-a,24:10:00,24:10:10,stop-b,2,0,0\n"
            "trip-b,25:01:00,25:01:30,stop-a,1,0,0\n"
            "trip-b,25:11:00,25:11:30,stop-b,2,0,0\n"
            "trip-removed,12:00:00,12:00:30,stop-a,1,0,0\n"
        ),
        "calendar.txt": (
            "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,"
            "start_date,end_date\n"
            "weekday,1,1,1,1,1,0,0,20250101,20250131\n"
            "removed,1,1,1,1,1,0,0,20250101,20250131\n"
        ),
        "calendar_dates.txt": "service_id,date,exception_type\nremoved,20250101,2\n",
    }
    if stop_times_override is not None:
        files["stop_times.txt"] = stop_times_override
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return path


def _normalize(archive: Path) -> NormalizedSchedule:
    return normalize_schedule_archive(
        archive,
        source_object_id="gtfs-v1",
        source_uri="https://cdn.example.invalid/gtfs.zip",
        service_date=SERVICE_DATE,
        published_or_listed_at_utc=datetime(2025, 1, 1, tzinfo=UTC),
        downloaded_at_utc=NOW,
    )


def test_schedule_archive_is_deterministic_and_preserves_gtfs_semantics(tmp_path: Path) -> None:
    archive = _archive(tmp_path / "gtfs.zip")
    first = _normalize(archive)
    second = _normalize(archive)
    assert first.manifest() == second.manifest()
    assert first.partition_bytes() == second.partition_bytes()
    assert [row.trip_id for row in first.rows] == ["trip-a", "trip-a", "trip-b", "trip-b"]
    assert first.rows[0].scheduled_arrival_local_seconds == 24 * 3600
    assert first.rows[2].scheduled_arrival_local_seconds == 25 * 3600 + 60
    assert first.rows[0].parent_station_id == "place-a"
    assert first.rows[1].parent_station_id == "stop-b"


def test_schedule_writer_and_historical_store_are_immutable(tmp_path: Path) -> None:
    archive = _archive(tmp_path / "gtfs.zip")
    schedule = _normalize(archive)
    output = tmp_path / "normalized"
    write_normalized_schedule(schedule, output)
    assert json.loads((output / "manifest.json").read_text())["row_count"] == 4
    assert (output / "stop_times.jsonl").read_bytes() == schedule.partition_bytes()
    with pytest.raises(ValueError, match="fresh directory"):
        write_normalized_schedule(schedule, output)

    store = HistoricalObjectStore(tmp_path / "store")
    blob, manifest = store.record(schedule.source, archive.read_bytes())
    assert blob.is_file() and manifest.is_file()
    assert store.record(schedule.source, archive.read_bytes()) == (blob, manifest)
    with pytest.raises(ValueError, match="different bytes"):
        store.record(
            replace(schedule.source, source_uri="https://changed.invalid"), archive.read_bytes()
        )
    with pytest.raises(ValueError, match="digest does not match"):
        store.record(schedule.source, b"different")


def test_schedule_cli_exercises_primary_archive_workflow(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    archive = _archive(tmp_path / "gtfs.zip")
    result = main(
        [
            "--archive",
            str(archive),
            "--output",
            str(tmp_path / "normalized"),
            "--store",
            str(tmp_path / "store"),
            "--source-object-id",
            "gtfs-v1",
            "--source-uri",
            "https://cdn.example.invalid/gtfs.zip",
            "--service-date",
            "2025-01-01",
            "--published-at",
            "2025-01-01T00:00:00Z",
            "--downloaded-at",
            "2025-01-02T00:00:00Z",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["row_count"] == 4
    assert len(payload["partition_sha256"]) == 64


def test_schedule_ingestion_rejects_unknown_references_and_invalid_time(tmp_path: Path) -> None:
    archive = _archive(
        tmp_path / "gtfs.zip",
        stop_times_override=(
            "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
            "unknown,12:00:00,12:00:00,stop-a,1\n"
        ),
    )
    with pytest.raises(ValueError, match="unknown trip_id"):
        _normalize(archive)
