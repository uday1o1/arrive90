"""Deterministic point-in-time GTFS Schedule archive normalization."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from arrive90_data_contracts.realtime import HistoricalSourceObject, SourceKind
from arrive90_data_contracts.schedule import ScheduleStopTime

from arrive90_ingestion.archive import ArchiveLimits, extract_zip
from arrive90_ingestion.historical import HistoricalObjectStore, canonical_json_bytes

SCHEMA_COLUMNS = tuple(ScheduleStopTime.__dataclass_fields__)
SCHEMA_FINGERPRINT = hashlib.sha256("\n".join(SCHEMA_COLUMNS).encode()).hexdigest()


@dataclass(frozen=True)
class NormalizedSchedule:
    source: HistoricalSourceObject
    service_date: date
    schedule_version_id: str
    rows: tuple[ScheduleStopTime, ...]

    def manifest(self) -> dict[str, object]:
        return {
            "row_count": len(self.rows),
            "schedule_version_id": self.schedule_version_id,
            "schema_columns": SCHEMA_COLUMNS,
            "schema_fingerprint": SCHEMA_FINGERPRINT,
            "service_date": self.service_date.isoformat(),
            "source": asdict(self.source),
        }

    def partition_bytes(self) -> bytes:
        return b"".join(canonical_json_bytes(asdict(row)) for row in self.rows)


def _read_rows(root: Path, name: str, *, required: bool = True) -> list[dict[str, str]]:
    path = root / name
    if not path.is_file():
        if required:
            raise ValueError(f"required GTFS file is missing: {name}")
        return []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return [dict(row) for row in csv.DictReader(stream)]


def _index_unique(
    rows: Iterable[Mapping[str, str]], key: str, source: str
) -> dict[str, Mapping[str, str]]:
    indexed: dict[str, Mapping[str, str]] = {}
    for row in rows:
        value = row.get(key, "")
        if not value:
            raise ValueError(f"{source} contains an empty {key}")
        if value in indexed:
            raise ValueError(f"{source} contains duplicate {key}: {value}")
        indexed[value] = row
    return indexed


def _gtfs_date(value: str) -> date:
    return datetime.strptime(value, "%Y%m%d").date()


def _gtfs_seconds(value: str) -> int:
    parts = value.split(":")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise ValueError(f"invalid GTFS local time: {value}")
    hours, minutes, seconds = (int(part) for part in parts)
    if minutes > 59 or seconds > 59:
        raise ValueError(f"invalid GTFS local time: {value}")
    return hours * 3600 + minutes * 60 + seconds


def _active_services(root: Path, service_date: date) -> set[str]:
    weekday = service_date.strftime("%A").lower()
    active: set[str] = set()
    for row in _read_rows(root, "calendar.txt", required=False):
        if (
            _gtfs_date(row["start_date"]) <= service_date <= _gtfs_date(row["end_date"])
            and row.get(weekday) == "1"
        ):
            active.add(row["service_id"])
    matching_exceptions = [
        row
        for row in _read_rows(root, "calendar_dates.txt", required=False)
        if _gtfs_date(row["date"]) == service_date
    ]
    for row in matching_exceptions:
        if row["exception_type"] == "1":
            active.add(row["service_id"])
        elif row["exception_type"] == "2":
            active.discard(row["service_id"])
        else:
            raise ValueError(f"invalid calendar exception_type: {row['exception_type']}")
    return active


def _feed_metadata(root: Path, service_date: date) -> tuple[str, date, date]:
    rows = _read_rows(root, "feed_info.txt", required=False)
    if not rows:
        return "UNVERSIONED", service_date, service_date
    row = rows[0]
    start = _gtfs_date(row.get("feed_start_date", service_date.strftime("%Y%m%d")))
    end = _gtfs_date(row.get("feed_end_date", service_date.strftime("%Y%m%d")))
    return row.get("feed_version") or "UNVERSIONED", start, end


def normalize_schedule_archive(
    archive: Path,
    *,
    source_object_id: str,
    source_uri: str,
    service_date: date,
    published_or_listed_at_utc: datetime | None,
    downloaded_at_utc: datetime,
    limits: ArchiveLimits | None = None,
) -> NormalizedSchedule:
    """Safely extract and normalize one service date from a GTFS archive."""

    body = archive.read_bytes()
    digest = hashlib.sha256(body).hexdigest()
    with tempfile.TemporaryDirectory(prefix="arrive90-gtfs-") as directory:
        root = Path(directory) / "archive"
        extract_zip(archive, root, limits=limits)
        feed_version, active_start, active_end = _feed_metadata(root, service_date)
        schedule_version_id = hashlib.sha256(
            f"{digest}:{feed_version}:{active_start}:{active_end}".encode()
        ).hexdigest()
        source = HistoricalSourceObject(
            source_object_id=source_object_id,
            source_kind=SourceKind.GTFS_ARCHIVE,
            source_uri=source_uri,
            published_or_listed_at_utc=published_or_listed_at_utc,
            downloaded_at_utc=downloaded_at_utc,
            blob_sha256=digest,
            schema_fingerprint=SCHEMA_FINGERPRINT,
            parser_version="arrive90-gtfs-schedule-v1",
        )
        known_at = published_or_listed_at_utc or downloaded_at_utc
        active_services = _active_services(root, service_date)
        routes = _index_unique(_read_rows(root, "routes.txt"), "route_id", "routes.txt")
        trips = _index_unique(_read_rows(root, "trips.txt"), "trip_id", "trips.txt")
        stops = _index_unique(_read_rows(root, "stops.txt"), "stop_id", "stops.txt")
        normalized: list[ScheduleStopTime] = []
        seen_keys: set[tuple[str, int]] = set()
        for stop_time in _read_rows(root, "stop_times.txt"):
            trip_id = stop_time.get("trip_id", "")
            if trip_id not in trips:
                raise ValueError(f"stop_times.txt references unknown trip_id: {trip_id}")
            trip = trips[trip_id]
            if trip.get("service_id") not in active_services:
                continue
            if trip.get("route_id") not in routes:
                raise ValueError(f"trips.txt references unknown route_id: {trip.get('route_id')}")
            stop_id = stop_time.get("stop_id", "")
            if stop_id not in stops:
                raise ValueError(f"stop_times.txt references unknown stop_id: {stop_id}")
            stop = stops[stop_id]
            sequence = int(stop_time["stop_sequence"])
            key = (trip_id, sequence)
            if key in seen_keys:
                raise ValueError(f"duplicate trip stop sequence: {trip_id}/{sequence}")
            seen_keys.add(key)
            normalized.append(
                ScheduleStopTime(
                    schedule_version_id=schedule_version_id,
                    feed_version=feed_version,
                    published_at_utc=published_or_listed_at_utc,
                    known_at_utc=known_at,
                    active_start_date=active_start,
                    active_end_date=active_end,
                    service_date=service_date,
                    service_id=trip["service_id"],
                    route_id=trip["route_id"],
                    direction_id=int(trip.get("direction_id") or 0),
                    trip_id=trip_id,
                    block_id=trip.get("block_id") or None,
                    stop_id=stop_id,
                    parent_station_id=stop.get("parent_station") or stop_id,
                    stop_sequence=sequence,
                    scheduled_arrival_local_seconds=_gtfs_seconds(stop_time["arrival_time"]),
                    scheduled_departure_local_seconds=_gtfs_seconds(stop_time["departure_time"]),
                    pickup_type=int(stop_time.get("pickup_type") or 0),
                    drop_off_type=int(stop_time.get("drop_off_type") or 0),
                    wheelchair_accessibility=int(trip.get("wheelchair_accessible") or 0),
                )
            )
    return NormalizedSchedule(
        source=source,
        service_date=service_date,
        schedule_version_id=schedule_version_id,
        rows=tuple(
            sorted(
                normalized, key=lambda row: (row.trip_id.encode(), row.stop_sequence, row.stop_id)
            )
        ),
    )


def write_normalized_schedule(schedule: NormalizedSchedule, output: Path) -> None:
    """Write an immutable deterministic manifest and partition into a fresh directory."""

    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("normalized schedule output must be a fresh directory")
    output.mkdir(parents=True, exist_ok=True)
    (output / "manifest.json").write_bytes(canonical_json_bytes(schedule.manifest()))
    (output / "stop_times.jsonl").write_bytes(schedule.partition_bytes())


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise argparse.ArgumentTypeError("timestamp must be timezone-aware UTC")
    return parsed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--source-object-id", required=True)
    parser.add_argument("--source-uri", required=True)
    parser.add_argument("--service-date", type=date.fromisoformat, required=True)
    parser.add_argument("--downloaded-at", type=_utc, required=True)
    parser.add_argument("--published-at", type=_utc)
    arguments = parser.parse_args(argv)
    schedule = normalize_schedule_archive(
        arguments.archive,
        source_object_id=arguments.source_object_id,
        source_uri=arguments.source_uri,
        service_date=arguments.service_date,
        published_or_listed_at_utc=arguments.published_at,
        downloaded_at_utc=arguments.downloaded_at,
    )
    HistoricalObjectStore(arguments.store).record(schedule.source, arguments.archive.read_bytes())
    write_normalized_schedule(schedule, arguments.output)
    print(
        json.dumps(
            {
                "manifest_sha256": hashlib.sha256(
                    canonical_json_bytes(schedule.manifest())
                ).hexdigest(),
                "partition_sha256": hashlib.sha256(schedule.partition_bytes()).hexdigest(),
                "row_count": len(schedule.rows),
                "schedule_version_id": schedule.schedule_version_id,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
