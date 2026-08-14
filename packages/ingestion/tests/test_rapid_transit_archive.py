from __future__ import annotations

import csv
import io
import json
import sys
import zipfile
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest
from arrive90_data_contracts.schedule import ArrivalEvidence
from arrive90_ingestion import rapid_transit_archive
from arrive90_ingestion.archive import ArchiveLimits
from arrive90_ingestion.rapid_transit_archive import (
    ArchivedRapidTransitEvent,
    SourceDiscoveryError,
    SourceProfile,
    _download_archive,
    _download_metadata,
    _producer_service_seconds_drift_is_classified,
    _service_seconds,
    _validate_url,
    deterministic_sample_dates,
    inspect_archive,
    load_source_profile,
    normalize_archived_arrivals,
    run_discovery,
    sha256_file,
    validate_metadata,
)

FIELDS = (
    "service_date",
    "route_id",
    "trip_id",
    "direction_id",
    "stop_id",
    "stop_sequence",
    "vehicle_id",
    "vehicle_label",
    "event_type",
    "event_time",
    "event_time_sec",
)
ROUTES_BY_MODE = {
    "HR": ("Red", "Orange", "Blue"),
    "LR": ("Green-B", "Green-C", "Green-D", "Green-E", "Mattapan"),
}


def _rows(year: int, month: int, mode: str) -> list[dict[str, object]]:
    current = date(year, month, 1)
    rows: list[dict[str, object]] = []
    while current.month == month:
        for route_index, route in enumerate(ROUTES_BY_MODE[mode]):
            for event_index, event_type in enumerate(("ARR", "DEP", "PRA", "PRD")):
                observed = datetime.combine(current, time(12, route_index, event_index), tzinfo=UTC)
                epoch = int(observed.timestamp())
                rows.append(
                    {
                        "service_date": current.isoformat(),
                        "route_id": route,
                        "trip_id": f"{current.isoformat()}-{route}",
                        "direction_id": 0,
                        "stop_id": f"stop-{route}",
                        "stop_sequence": 10,
                        "vehicle_id": f"vehicle-{route}",
                        "vehicle_label": route,
                        "event_type": event_type,
                        "event_time": epoch,
                        "event_time_sec": _service_seconds(epoch, current),
                    }
                )
        current += timedelta(days=1)
    return rows


def _archive(path: Path, *, duplicate: bool = False) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for month in range(1, 13):
            for mode in ("HR", "LR"):
                buffer = io.StringIO(newline="")
                writer = csv.DictWriter(buffer, fieldnames=FIELDS, lineterminator="\r\n")
                writer.writeheader()
                rows = _rows(2022, month, mode)
                writer.writerows(rows)
                if duplicate and month == 1 and mode == "HR":
                    writer.writerow(rows[0])
                archive.writestr(
                    f"Events_2022/2022-{month:02d}_{mode}Events.csv",
                    buffer.getvalue().encode(),
                )
    return path


def _profile(archive: Path) -> SourceProfile:
    return SourceProfile(
        profile_path=archive.parent / "profile.yaml",
        source_profile_version="fixture-v1",
        source_id="fixture",
        item_id="item",
        metadata_url="https://www.arcgis.com/item?f=pjson",
        archive_url="https://www.arcgis.com/item/data",
        allowed_host="www.arcgis.com",
        expected_owner="MBTAHUB_ADMIN",
        expected_access="public",
        expected_license="CC0",
        expected_name="Events_2022.zip",
        expected_title="MBTA Rapid Transit Events 2022",
        expected_size_bytes=archive.stat().st_size,
        expected_modified_at_utc=datetime(2025, 1, 1, tzinfo=UTC),
        expected_archive_sha256=sha256_file(archive),
        archive_root="Events_2022",
        archive_year=2022,
        archive_modes=("HR", "LR"),
        expected_member_count=24,
        archive_limits=ArchiveLimits(maximum_expansion_ratio=1000),
        schema_fields=FIELDS,
        event_types=("ARR", "DEP", "PRA", "PRD"),
        routes=tuple(sorted(route for routes in ROUTES_BY_MODE.values() for route in routes)),
        producer_repository="https://github.com/mbta/transit-performance",
        producer_commit="a" * 40,
        producer_file_hashes={"one": "b" * 64, "two": "c" * 64, "three": "d" * 64},
        license_identifier="CC0-1.0",
        attribution="MBTA",
        raw_archive_redistribution="not-vendored",
        project_artifact_policy="synthetic-only",
    )


def _metadata(profile: SourceProfile) -> bytes:
    return json.dumps(
        {
            "id": profile.item_id,
            "owner": profile.expected_owner,
            "access": profile.expected_access,
            "name": profile.expected_name,
            "title": profile.expected_title,
            "size": profile.expected_size_bytes,
            "licenseInfo": profile.expected_license,
            "modified": int(profile.expected_modified_at_utc.timestamp() * 1000),
        }
    ).encode()


class _Response(io.BytesIO):
    def __init__(self, body: bytes, url: str) -> None:
        super().__init__(body)
        self._url = url
        self.headers = {"Content-Length": str(len(body))}

    def geturl(self) -> str:
        return self._url

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class _Opener:
    def __init__(self, body: bytes, url: str) -> None:
        self.body = body
        self.url = url

    def open(self, _request: object, *, timeout: int) -> _Response:
        assert timeout in {30, 60}
        return _Response(self.body, self.url)


def test_full_archive_contract_and_frozen_sample_are_deterministic(tmp_path: Path) -> None:
    archive = _archive(tmp_path / "events.zip")
    report = inspect_archive(_profile(archive), archive, "public-seed")
    assert report["status"] == "PASSED"
    assert report["member_count"] == 24
    assert report["service_date_count"] == 365
    assert report["row_count"] == 365 * 8 * 4
    assert report["event_type_counts"] == {
        "ARR": 365 * 8,
        "DEP": 365 * 8,
        "PRA": 365 * 8,
        "PRD": 365 * 8,
    }
    assert deterministic_sample_dates(2022, "public-seed") == deterministic_sample_dates(
        2022, "public-seed"
    )
    assert date(2022, 3, 13) in deterministic_sample_dates(2022, "public-seed")


def test_only_known_producer_service_second_drift_is_classified() -> None:
    assert _producer_service_seconds_drift_is_classified({-3600, 0, 1, 3600})
    assert not _producer_service_seconds_drift_is_classified(set())
    assert not _producer_service_seconds_drift_is_classified({2})


def test_duplicate_event_unit_is_quantified_with_stable_source_rows(tmp_path: Path) -> None:
    archive = _archive(tmp_path / "events.zip", duplicate=True)
    report = inspect_archive(_profile(archive), archive, "public-seed")
    assert report["status"] == "PASSED"
    assert report["repeated_event_units"] == 1
    assert report["duplicate_semantic_events"] == 1
    assert report["checks"]["source_rows_have_stable_member_and_row_identity"] is True
    assert report["duplicate_semantic_event_examples"][0]["row_number"] > 0


def test_metadata_and_url_identity_are_fail_closed(tmp_path: Path) -> None:
    archive = _archive(tmp_path / "events.zip")
    profile = _profile(archive)
    metadata = {
        "id": "item",
        "owner": "MBTAHUB_ADMIN",
        "access": "public",
        "name": "Events_2022.zip",
        "title": "MBTA Rapid Transit Events 2022",
        "size": archive.stat().st_size,
        "licenseInfo": "CC0",
        "modified": int(datetime(2025, 1, 1, tzinfo=UTC).timestamp() * 1000),
    }
    assert validate_metadata(profile, json.dumps(metadata).encode())["owner"] == "MBTAHUB_ADMIN"
    metadata["licenseInfo"] = "unknown"
    with pytest.raises(SourceDiscoveryError, match="metadata does not match"):
        validate_metadata(profile, json.dumps(metadata).encode())
    _validate_url(profile.archive_url, profile, profile.archive_url)
    with pytest.raises(SourceDiscoveryError, match="pinned HTTPS host"):
        _validate_url("http://example.com/item/data", profile, profile.archive_url)


def test_repository_source_profile_loads_exact_inventory() -> None:
    profile = load_source_profile(Path("configs/sources/mbta-rapid-transit-events-2022.yaml"))
    assert profile.expected_member_count == 24
    assert profile.expected_members[0] == "Events_2022/2022-01_HREvents.csv"
    assert profile.expected_members[-1] == "Events_2022/2022-12_LREvents.csv"
    assert profile.license_identifier == "CC0-1.0"


def test_discovery_writes_separate_runtime_and_compact_reports(tmp_path: Path) -> None:
    archive = _archive(tmp_path / "events.zip")
    profile = _profile(archive)
    profile.profile_path.write_text("fixture-profile\n", encoding="utf-8")
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_bytes(_metadata(profile))
    charter = tmp_path / "acceptance.yaml"
    charter.write_text("query_generation:\n  public_seed: public-seed\n", encoding="utf-8")
    runtime = tmp_path / "runtime" / "manifest.json"
    report_path = tmp_path / "reports" / "source.json"

    report = run_discovery(
        profile=profile,
        metadata_path=metadata_path,
        archive_path=archive,
        acceptance_charter=charter,
        acquired_at_utc=datetime(2026, 1, 1, tzinfo=UTC),
        runtime_manifest_path=runtime,
        report_path=report_path,
    )

    assert report["status"] == "PASSED"
    assert report["milestone_0_accepted"] is False
    assert json.loads(report_path.read_text(encoding="utf-8")) == report
    manifest = json.loads(runtime.read_text(encoding="utf-8"))
    assert manifest["archive"]["row_count"] == 365 * 8 * 4
    assert manifest["conservative_product_available_at_utc"].startswith("2026-01-01")
    assert report["runtime_manifest_sha256"] == sha256_file(runtime)


def test_bounded_download_helpers_persist_only_pinned_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_archive = _archive(tmp_path / "source.zip")
    profile = _profile(source_archive)
    metadata_body = _metadata(profile)
    monkeypatch.setattr(
        rapid_transit_archive,
        "_opener",
        lambda _profile, expected_url: _Opener(
            metadata_body if expected_url == profile.metadata_url else source_archive.read_bytes(),
            expected_url,
        ),
    )

    metadata_path = tmp_path / "raw" / "metadata.json"
    assert _download_metadata(profile, metadata_path) == metadata_body
    assert metadata_path.read_bytes() == metadata_body

    archive_path = tmp_path / "raw" / "downloaded.zip"
    acquired = _download_archive(profile, archive_path)
    assert acquired.tzinfo is UTC
    assert sha256_file(archive_path) == profile.expected_archive_sha256
    assert _download_archive(profile, archive_path).tzinfo is UTC

    archive_path.write_bytes(b"not pinned")
    with pytest.raises(SourceDiscoveryError, match="unpinned bytes"):
        _download_archive(profile, archive_path)


def test_cli_runs_local_inputs_and_rejects_ambiguous_modes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observed: dict[str, Any] = {}

    def fake_discovery(**kwargs: Any) -> dict[str, Any]:
        observed.update(kwargs)
        return {"status": "PASSED", "milestone_0_accepted": False, "failing_checks": []}

    monkeypatch.setattr(rapid_transit_archive, "run_discovery", fake_discovery)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "arrive90-discover-rapid-transit-source",
            "--metadata",
            str(tmp_path / "metadata.json"),
            "--archive",
            str(tmp_path / "archive.zip"),
            "--acquired-at-utc",
            "2026-01-01T00:00:00+00:00",
        ],
    )
    rapid_transit_archive.main()
    assert observed["acquired_at_utc"] == datetime(2026, 1, 1, tzinfo=UTC)
    assert '"status": "PASSED"' in capsys.readouterr().out

    monkeypatch.setattr(
        sys,
        "argv",
        ["arrive90-discover-rapid-transit-source", "--download", "--archive", "x"],
    )
    with pytest.raises(SystemExit, match="cannot be combined"):
        rapid_transit_archive.main()

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "arrive90-discover-rapid-transit-source",
            "--metadata",
            "metadata.json",
            "--archive",
            "archive.zip",
        ],
    )
    with pytest.raises(SystemExit, match="required for local inputs"):
        rapid_transit_archive.main()


def _event(
    key: str,
    event_type: str,
    sequence: int,
    observed: datetime,
    *,
    vehicle: str = "vehicle",
) -> ArchivedRapidTransitEvent:
    return ArchivedRapidTransitEvent(
        source_row_key=key,
        source_member="member.csv",
        service_date=date(2022, 1, 1),
        route_id="Red",
        direction_id=0,
        trip_id="trip",
        vehicle_id=vehicle,
        stop_id=f"stop-{sequence}",
        stop_sequence=sequence,
        event_type=event_type,
        event_time_utc=observed,
        product_available_at_utc=datetime(2025, 1, 1, tzinfo=UTC),
    )


def test_archived_arrival_is_an_upper_bound_and_predictions_never_promote() -> None:
    start = datetime(2022, 1, 1, 12, tzinfo=UTC)
    events = (
        _event("departure", "DEP", 1, start),
        _event("arrival", "ARR", 2, start + timedelta(seconds=90)),
        _event("proxy", "PRA", 3, start + timedelta(seconds=180)),
    )
    evidence = normalize_archived_arrivals(events)
    assert len(evidence) == 1
    assert evidence[0].arrival_evidence is ArrivalEvidence.VP_STOPPED_AT
    assert evidence[0].arrival_lower_bound_utc == start
    assert evidence[0].arrival_upper_bound_utc == start + timedelta(seconds=90)
    assert evidence[0].arrival_lower_bound_utc != evidence[0].arrival_upper_bound_utc

    promoted = tuple(
        _event("departure", "DEP", 1, start)
        if event.event_type == "DEP"
        else _event("seeded-proxy", "PRA", 2, start + timedelta(seconds=90))
        for event in events[:2]
    )
    assert normalize_archived_arrivals(promoted) == ()


def test_reused_vehicle_identity_and_contradictory_order_are_quarantined() -> None:
    start = datetime(2022, 1, 1, 12, tzinfo=UTC)
    with pytest.raises(SourceDiscoveryError, match="reused"):
        normalize_archived_arrivals(
            (
                _event("one", "ARR", 1, start, vehicle="one"),
                _event("two", "ARR", 1, start, vehicle="two"),
            )
        )
    with pytest.raises(SourceDiscoveryError, match="ordering"):
        normalize_archived_arrivals(
            (
                _event("later-sequence", "DEP", 2, start),
                _event("earlier-sequence", "ARR", 1, start + timedelta(seconds=1)),
            )
        )
