from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest
from arrive90_ingestion.acquisition import (
    AcquisitionError,
    DownloadResult,
    HttpObjectMetadata,
    acquisition_content_entry,
    schedule_derived_entry,
    sha256_file,
    sqlite_schema_fingerprint,
    write_acquisition_lock,
)
from arrive90_ingestion.cli import main
from arrive90_ingestion.inventory import FIRST_BOUNDARY_DATE
from arrive90_ingestion.year_acquisition import (
    FullYearAcquisitionResult,
    acquire_full_year,
)

ACCEPTANCE_VERSION = "travel-time-v1.1"
ACQUIRED_AT = datetime(2026, 8, 14, 12, tzinfo=UTC)
LAST_MODIFIED = datetime(2025, 11, 28, 12, tzinfo=UTC)


def _inventory(path: Path, source: Path) -> Path:
    entries = []
    for offset in range(368):
        inventory_date = FIRST_BOUNDARY_DATE + timedelta(days=offset)
        filename = f"COMPACTED_mbta_all_{inventory_date.isoformat()}_13:42:00.parquet"
        key = f"feeds/mbta_all/{filename}"
        entries.append(
            {
                "declared_size_mb": max(source.stat().st_size / 1024 / 1024, 0.01),
                "inventory_date": inventory_date.isoformat(),
                "inventory_generated_at": "2026-08-14T05:00:51+00:00",
                "inventory_snapshot_sha256": "a" * 64,
                "inventory_snapshot_url": (
                    "https://busobservatory-lake.s3.amazonaws.com/index/data-inventory.json"
                ),
                "source_object_key": key,
                "source_url": f"https://busobservatory-lake.s3.amazonaws.com/{key}",
            }
        )
    path.write_text(
        json.dumps({"acceptance_version": ACCEPTANCE_VERSION, "entries": entries}),
        encoding="utf-8",
    )
    return path


def _schedule(raw_root: Path, lock_path: Path) -> tuple[Path, Path]:
    schedule_root = raw_root / "mbta-gtfs" / "2024"
    schedule_root.mkdir(parents=True)
    archive = schedule_root / "GTFS_ARCHIVE.db.gz"
    archive.write_bytes(b"schedule archive fixture")
    database = schedule_root / "GTFS_ARCHIVE.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE feed_info (feed_version TEXT)")
    downloaded = DownloadResult(
        path=archive,
        size_bytes=archive.stat().st_size,
        sha256=sha256_file(archive),
        etag="schedule-etag",
        last_modified_at_utc=LAST_MODIFIED,
        downloaded_at_utc=ACQUIRED_AT,
    )
    content = acquisition_content_entry(
        downloaded,
        source_object_key="lamp/gtfs_archive/2024/GTFS_ARCHIVE.db.gz",
        source_url="https://performancedata.mbta.com/lamp/gtfs_archive/2024/GTFS_ARCHIVE.db.gz",
        schema_fingerprint="b" * 64,
        row_count=1,
        parser_version="test-schedule-v1",
    )
    derived = schedule_derived_entry(
        compressed_sha256=downloaded.sha256,
        expanded_path=database,
        expanded_sha256=sha256_file(database),
        schema_fingerprint=sqlite_schema_fingerprint(database),
    )
    write_acquisition_lock(
        lock_path,
        content_entries=(content,),
        derived_entries=(derived,),
    )
    return archive, database


def _inputs(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    source = tmp_path / "source.parquet"
    pq.write_table(pa.table({"value": [1, 2]}), source)
    inventory = _inventory(tmp_path / "inventory.json", source)
    raw_root = tmp_path / "raw"
    pinned_lock = tmp_path / "pinned.json"
    _schedule(raw_root, pinned_lock)
    return source, inventory, raw_root, pinned_lock, tmp_path / "full.json"


def test_full_year_acquisition_bootstraps_and_reverifies_exact_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, inventory, raw_root, pinned_lock, full_lock = _inputs(tmp_path)
    metadata_calls = 0

    def fake_metadata(_url: str, **_kwargs: object) -> HttpObjectMetadata:
        nonlocal metadata_calls
        metadata_calls += 1
        return HttpObjectMetadata(source.stat().st_size, "vehicle-etag", LAST_MODIFIED)

    def fake_download(
        _url: str,
        destination: Path,
        **kwargs: object,
    ) -> DownloadResult:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
        return DownloadResult(
            path=destination,
            size_bytes=source.stat().st_size,
            sha256=sha256_file(source),
            etag=str(kwargs["expected_etag"]),
            last_modified_at_utc=LAST_MODIFIED,
            downloaded_at_utc=ACQUIRED_AT,
        )

    monkeypatch.setattr(
        "arrive90_ingestion.year_acquisition.fetch_http_object_metadata", fake_metadata
    )
    monkeypatch.setattr("arrive90_ingestion.year_acquisition.download_resumable", fake_download)
    first = acquire_full_year(
        2024,
        inventory_lock_path=inventory,
        pinned_acquisition_lock_path=pinned_lock,
        raw_root=raw_root,
        acquisition_lock_path=full_lock,
        workers=4,
    )
    first_bytes = full_lock.read_bytes()
    assert first.object_count == 368
    assert first.total_row_count == 736
    assert metadata_calls == 368
    assert len(json.loads(first_bytes)["content_entries"]) == 369

    def metadata_must_not_run(_url: str, **_kwargs: object) -> HttpObjectMetadata:
        raise AssertionError("a committed content lock must replace metadata discovery")

    monkeypatch.setattr(
        "arrive90_ingestion.year_acquisition.fetch_http_object_metadata",
        metadata_must_not_run,
    )
    second = acquire_full_year(
        2024,
        inventory_lock_path=inventory,
        pinned_acquisition_lock_path=pinned_lock,
        raw_root=raw_root,
        acquisition_lock_path=full_lock,
        workers=2,
    )
    assert second == first
    assert full_lock.read_bytes() == first_bytes


def test_full_year_acquisition_rejects_scope_lock_and_worker_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, inventory, raw_root, pinned_lock, full_lock = _inputs(tmp_path)
    with pytest.raises(AcquisitionError, match="frozen to 2024"):
        acquire_full_year(2023, inventory_lock_path=inventory)
    with pytest.raises(AcquisitionError, match="workers"):
        acquire_full_year(2024, inventory_lock_path=inventory, workers=0)

    def fake_metadata(_url: str, **_kwargs: object) -> HttpObjectMetadata:
        return HttpObjectMetadata(source.stat().st_size, "vehicle-etag", LAST_MODIFIED)

    def fake_download(_url: str, destination: Path, **kwargs: object) -> DownloadResult:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
        return DownloadResult(
            path=destination,
            size_bytes=source.stat().st_size,
            sha256=sha256_file(source),
            etag=str(kwargs["expected_etag"]),
            last_modified_at_utc=LAST_MODIFIED,
            downloaded_at_utc=ACQUIRED_AT,
        )

    monkeypatch.setattr(
        "arrive90_ingestion.year_acquisition.fetch_http_object_metadata", fake_metadata
    )
    monkeypatch.setattr("arrive90_ingestion.year_acquisition.download_resumable", fake_download)
    acquire_full_year(
        2024,
        inventory_lock_path=inventory,
        pinned_acquisition_lock_path=pinned_lock,
        raw_root=raw_root,
        acquisition_lock_path=full_lock,
    )
    payload = json.loads(full_lock.read_text(encoding="utf-8"))
    vehicle = next(
        entry
        for entry in payload["content_entries"]
        if entry["source_object_key"].endswith("parquet")
    )
    vehicle["sha256"] = "f" * 64
    full_lock.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(AcquisitionError, match="does not match"):
        acquire_full_year(
            2024,
            inventory_lock_path=inventory,
            pinned_acquisition_lock_path=pinned_lock,
            raw_root=raw_root,
            acquisition_lock_path=full_lock,
        )


def test_public_cli_dispatches_complete_year_acquisition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = FullYearAcquisitionResult(
        acquisition_lock_path=tmp_path / "full.json",
        acquisition_lock_sha256="a" * 64,
        object_count=368,
        total_size_bytes=100,
        total_row_count=200,
        schema_fingerprints=("b" * 64,),
        schedule_database_sha256="c" * 64,
    )
    monkeypatch.setattr(
        "arrive90_ingestion.cli.acquire_full_year", lambda *_args, **_kwargs: result
    )
    assert main(["source", "download", "--year", "2024"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["object_count"] == 368
    assert payload["total_row_count"] == 200

    assert main(["source", "download", "--year", "2024", "--include-schedule"]) == 1
    assert "implicit" in capsys.readouterr().err


def test_inventory_fixture_covers_exact_boundary_dates(tmp_path: Path) -> None:
    source = tmp_path / "source.parquet"
    source.write_bytes(b"source")
    payload = json.loads(_inventory(tmp_path / "inventory.json", source).read_text())
    dates = [date.fromisoformat(entry["inventory_date"]) for entry in payload["entries"]]
    assert dates[0] == date(2023, 12, 31)
    assert dates[-1] == date(2025, 1, 1)
