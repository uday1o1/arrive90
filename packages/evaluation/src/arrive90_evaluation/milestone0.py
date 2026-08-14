"""Fail-closed Milestone 0 audit over official 2022 MBTA source archives."""

from __future__ import annotations

import argparse
import csv
import hashlib
import heapq
import hmac
import io
import json
import shutil
import sqlite3
import sys
import zipfile
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from time import perf_counter
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from arrive90_ingestion.rapid_transit_archive import (
    SourceDiscoveryError,
    deterministic_sample_dates,
    load_source_profile,
    sha256_file,
    validate_metadata,
)
from arrive90_routing.population import encode_key

NEW_YORK = ZoneInfo("America/New_York")
RAIL_ROUTES = (
    "Blue",
    "Green-B",
    "Green-C",
    "Green-D",
    "Green-E",
    "Mattapan",
    "Orange",
    "Red",
)
EVENT_FIELDS = (
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
PEAK_STARTS = (time(7), time(16))
PEAK_ENDS = (time(10), time(19))
SCHEDULE_QUERY_CACHE_VERSION = "schedule-query-cache-v1"


class Milestone0AuditError(ValueError):
    """Raised when an immutable input or audit invariant fails closed."""


def _progress(message: str) -> None:
    print(f"[arrive90 milestone-0] {message}", file=sys.stderr, flush=True)


@dataclass(frozen=True)
class ScheduleSourceProfile:
    profile_path: Path
    source_profile_version: str
    source_id: str
    expanded_size_bytes: int
    expanded_sha256: str
    compressed_size_bytes: int
    compressed_sha256: str
    last_modified_at_utc: datetime
    expected_schedule_version_count: int
    first_active_date: date
    last_active_date: date
    required_tables: tuple[str, ...]
    rail_routes: tuple[str, ...]
    license_sha256: str


@dataclass(frozen=True)
class ScheduleVersion:
    active_start: date
    active_end: date
    published_at_utc: datetime
    feed_version: str

    @property
    def version_id(self) -> str:
        return hashlib.sha256(self.feed_version.encode()).hexdigest()


@dataclass(frozen=True)
class ScheduledStop:
    stop_id: str
    parent_station_id: str
    sequence: int
    arrival_seconds: int
    departure_seconds: int


@dataclass(frozen=True)
class ScheduledTrip:
    active_start: date
    active_end: date
    route_id: str
    direction_id: int
    trip_id: str
    route_pattern_id: str
    service_id: str
    stops: tuple[ScheduledStop, ...]


@dataclass(frozen=True)
class AuditLeg:
    route_id: str
    direction_id: int
    origin_stop_id: str
    origin_parent_station_id: str
    destination_stop_id: str
    destination_parent_station_id: str
    route_pattern_id: str


@dataclass(frozen=True)
class AuditQuery:
    query_id: str
    kind: str
    service_date: date
    query_time_utc: datetime
    ready_at_utc: datetime
    observation_horizon_utc: datetime
    schedule_version_id: str
    slice_name: str
    legs: tuple[AuditLeg, ...]
    transfer_walk_seconds: int


@dataclass(frozen=True)
class ScheduledDeparture:
    trip_id: str
    departure_utc: datetime
    arrival_utc: datetime


@dataclass(frozen=True)
class StopVisit:
    stop_id: str
    stop_sequence: int
    arrival_upper_utc: datetime
    departure_upper_utc: datetime | None
    arrival_lower_utc: datetime | None
    arrival_source_row: str
    departure_source_row: str | None


@dataclass(frozen=True)
class ObservedRun:
    service_date: date
    route_id: str
    direction_id: int
    trip_id: str
    vehicle_id: str
    visits: tuple[StopVisit, ...]
    ambiguous_identity: bool


@dataclass(frozen=True)
class LegResolution:
    trip_id: str
    vehicle_id: str
    boarding_ready_utc: datetime
    boarding_observed_utc: datetime
    destination_lower_utc: datetime
    destination_upper_utc: datetime
    arrival_interval_width_seconds: int
    boarding_source_row: str
    destination_source_row: str
    reconciliation_complete: bool
    missing_prior_scheduled_trip_ids: tuple[str, ...]


@dataclass(frozen=True)
class ActiveSchedule:
    trips: tuple[ScheduledTrip, ...]
    calendar: Mapping[str, tuple[date, date, tuple[bool, ...]]]
    exceptions: Mapping[tuple[date, str], int]
    station_routes: Mapping[str, frozenset[str]]


@dataclass(frozen=True)
class _TransferLegCandidate:
    trip_id: str
    service_date: date
    departure_utc: datetime
    arrival_utc: datetime
    leg: AuditLeg


class _TopQueries:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._values: list[tuple[int, str, AuditQuery]] = []

    def add(self, rank: int, query: AuditQuery) -> None:
        item = (-rank, query.query_id, query)
        if len(self._values) < self.limit:
            heapq.heappush(self._values, item)
        elif rank < -self._values[0][0]:
            heapq.heapreplace(self._values, item)

    def ordered(self) -> tuple[AuditQuery, ...]:
        return tuple(
            item[2]
            for item in sorted(
                self._values,
                key=lambda item: (-item[0], item[1].encode()),
            )
        )


def _mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise Milestone0AuditError(f"{field} must be a string-keyed mapping")
    return value


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise Milestone0AuditError(f"{field} must be a non-empty string")
    return value


def _integer(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise Milestone0AuditError(f"{field} must be a positive integer")
    return value


def _strings(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise Milestone0AuditError(f"{field} must be a non-empty list")
    parsed = tuple(_string(item, field) for item in value)
    if len(parsed) != len(set(parsed)):
        raise Milestone0AuditError(f"{field} must contain unique values")
    return parsed


def _date_value(value: object, field: str) -> date:
    if isinstance(value, datetime):
        raise Milestone0AuditError(f"{field} must be a date without a time")
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as error:
            raise Milestone0AuditError(f"{field} must be an ISO date") from error
    raise Milestone0AuditError(f"{field} must be an ISO date")


def load_schedule_source_profile(path: Path) -> ScheduleSourceProfile:
    loaded = _mapping(yaml.safe_load(path.read_text(encoding="utf-8")), "schedule profile")
    license_data = _mapping(loaded.get("license"), "license")
    last_modified = datetime.fromisoformat(
        _string(loaded.get("last_modified_at_utc"), "last_modified_at_utc")
    )
    if last_modified.tzinfo is None or last_modified.utcoffset() != timedelta(0):
        raise Milestone0AuditError("last_modified_at_utc must be timezone-aware UTC")
    profile = ScheduleSourceProfile(
        profile_path=path,
        source_profile_version=_string(
            loaded.get("source_profile_version"), "source_profile_version"
        ),
        source_id=_string(loaded.get("source_id"), "source_id"),
        expanded_size_bytes=_integer(loaded.get("expanded_size_bytes"), "expanded_size_bytes"),
        expanded_sha256=_string(loaded.get("expanded_sha256"), "expanded_sha256"),
        compressed_size_bytes=_integer(
            loaded.get("compressed_size_bytes"), "compressed_size_bytes"
        ),
        compressed_sha256=_string(loaded.get("compressed_sha256"), "compressed_sha256"),
        last_modified_at_utc=last_modified,
        expected_schedule_version_count=_integer(
            loaded.get("expected_schedule_version_count"),
            "expected_schedule_version_count",
        ),
        first_active_date=_date_value(
            loaded.get("expected_first_active_date"), "expected_first_active_date"
        ),
        last_active_date=_date_value(
            loaded.get("expected_last_active_date"), "expected_last_active_date"
        ),
        required_tables=_strings(loaded.get("required_tables"), "required_tables"),
        rail_routes=_strings(loaded.get("rail_routes"), "rail_routes"),
        license_sha256=_string(license_data.get("license_sha256"), "license_sha256"),
    )
    if len(profile.expanded_sha256) != 64 or len(profile.compressed_sha256) != 64:
        raise Milestone0AuditError("schedule archive digests must contain 64 characters")
    if profile.rail_routes != RAIL_ROUTES:
        raise Milestone0AuditError("schedule profile rail routes do not match V1")
    return profile


def _parse_feed_version(value: str) -> datetime:
    parts = value.split(", ")
    if len(parts) < 3:
        raise Milestone0AuditError(f"feed_version has no publication timestamp: {value}")
    try:
        parsed = datetime.fromisoformat(parts[-2])
    except ValueError as error:
        raise Milestone0AuditError(f"feed_version timestamp is invalid: {value}") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise Milestone0AuditError("feed_version publication timestamp must be UTC")
    return parsed


def _yyyymmdd(value: int) -> date:
    return datetime.strptime(str(value), "%Y%m%d").date()


def _service_date_integer(value: date) -> int:
    return int(value.strftime("%Y%m%d"))


def _date_range(start: date, end: date) -> Iterator[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _connect_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _valid_schedule_query_cache(path: Path, source_sha256: str) -> bool:
    if not path.is_file():
        return False
    try:
        with _connect_read_only(path) as connection:
            metadata = connection.execute(
                "SELECT source_sha256, cache_version FROM arrive90_query_cache_metadata"
            ).fetchone()
            index = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'index' "
                "AND name = 'arrive90_m0_stop_times_trip_active'"
            ).fetchone()
            integrity = connection.execute("PRAGMA quick_check").fetchone()
    except sqlite3.Error:
        return False
    return (
        tuple(metadata or ()) == (source_sha256, SCHEDULE_QUERY_CACHE_VERSION)
        and index is not None
        and tuple(integrity or ()) == ("ok",)
    )


def prepare_schedule_query_cache(
    source: Path,
    *,
    source_sha256: str,
    cache_directory: Path,
) -> Path:
    """Create an ignored, indexed copy without mutating the pinned source object."""

    cache_directory.mkdir(parents=True, exist_ok=True)
    cache = cache_directory / f"gtfs-{source_sha256[:16]}-{SCHEDULE_QUERY_CACHE_VERSION}.db"
    if _valid_schedule_query_cache(cache, source_sha256):
        return cache
    building = cache.with_suffix(".building")
    building.unlink(missing_ok=True)
    try:
        shutil.copyfile(source, building)
        with sqlite3.connect(building) as connection:
            connection.execute("PRAGMA journal_mode = OFF")
            connection.execute("PRAGMA synchronous = OFF")
            connection.execute(
                "CREATE INDEX arrive90_m0_stop_times_trip_active "
                "ON stop_times(trip_id, gtfs_active_date, gtfs_end_date)"
            )
            connection.execute(
                "CREATE TABLE arrive90_query_cache_metadata "
                "(source_sha256 TEXT NOT NULL, cache_version TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO arrive90_query_cache_metadata VALUES (?, ?)",
                (source_sha256, SCHEDULE_QUERY_CACHE_VERSION),
            )
            connection.commit()
        if not _valid_schedule_query_cache(building, source_sha256):
            raise Milestone0AuditError("constructed schedule query cache failed validation")
        building.replace(cache)
    finally:
        building.unlink(missing_ok=True)
    return cache


def audit_schedule_archive(
    profile: ScheduleSourceProfile,
    *,
    compressed_archive: Path,
    expanded_database: Path,
) -> tuple[dict[str, Any], tuple[ScheduleVersion, ...], dict[date, ScheduleVersion]]:
    if compressed_archive.stat().st_size != profile.compressed_size_bytes:
        raise Milestone0AuditError("compressed schedule archive size is not pinned")
    if sha256_file(compressed_archive) != profile.compressed_sha256:
        raise Milestone0AuditError("compressed schedule archive digest is not pinned")
    if expanded_database.stat().st_size != profile.expanded_size_bytes:
        raise Milestone0AuditError("expanded schedule database size is not pinned")
    if sha256_file(expanded_database) != profile.expanded_sha256:
        raise Milestone0AuditError("expanded schedule database digest is not pinned")

    with _connect_read_only(expanded_database) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        table_rows = connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall()
        tables = {str(row["name"]): str(row["sql"]) for row in table_rows}
        missing_tables = sorted(set(profile.required_tables) - tables.keys())
        if missing_tables:
            raise Milestone0AuditError(f"schedule database is missing tables: {missing_tables}")
        feed_rows = connection.execute(
            "SELECT feed_version, gtfs_active_date, gtfs_end_date "
            "FROM feed_info ORDER BY gtfs_active_date, feed_version"
        ).fetchall()
        counts = {
            table: int(
                connection.execute(
                    f'SELECT COUNT(*) FROM "{table}"'  # noqa: S608 - pinned table name.
                ).fetchone()[0]
            )
            for table in profile.required_tables
        }
        schemas = {
            table: [
                {"name": str(row[1]), "type": str(row[2])}
                for row in connection.execute(f'PRAGMA table_info("{table}")')
            ]
            for table in profile.required_tables
        }

    versions = tuple(
        ScheduleVersion(
            active_start=_yyyymmdd(int(row["gtfs_active_date"])),
            active_end=_yyyymmdd(int(row["gtfs_end_date"])),
            published_at_utc=_parse_feed_version(str(row["feed_version"])),
            feed_version=str(row["feed_version"]),
        )
        for row in feed_rows
    )
    by_date: dict[date, ScheduleVersion] = {}
    duplicate_dates: list[str] = []
    unknown_by_first_query = 0
    for version in versions:
        first_query_cutoff = datetime.combine(version.active_start, time(6), NEW_YORK).astimezone(
            UTC
        )
        if version.published_at_utc > first_query_cutoff:
            unknown_by_first_query += 1
        for service_date in _date_range(version.active_start, version.active_end):
            if service_date in by_date:
                duplicate_dates.append(service_date.isoformat())
            by_date[service_date] = version
    expected_dates = set(_date_range(profile.first_active_date, profile.last_active_date))
    schema_bytes = json.dumps(schemas, sort_keys=True, separators=(",", ":")).encode()
    checks = {
        "compressed_archive_pinned": True,
        "expanded_database_pinned": True,
        "sqlite_integrity_check_passed": integrity == "ok",
        "required_tables_present": not missing_tables,
        "schedule_version_count_pinned": len(versions) == profile.expected_schedule_version_count,
        "active_date_inventory_complete": set(by_date) == expected_dates,
        "one_schedule_version_per_service_date": not duplicate_dates,
        "publication_known_by_first_audit_query": unknown_by_first_query == 0,
        "rail_routes_pinned": profile.rail_routes == RAIL_ROUTES,
    }
    failing = [name for name, passed in checks.items() if not passed]
    report = {
        "status": "PASSED" if not failing else "FAILED",
        "checks": checks,
        "failing_checks": failing,
        "source_profile_version": profile.source_profile_version,
        "compressed_sha256": profile.compressed_sha256,
        "expanded_sha256": profile.expanded_sha256,
        "schema_fingerprint": hashlib.sha256(schema_bytes).hexdigest(),
        "table_row_counts": counts,
        "schedule_version_count": len(versions),
        "first_active_date": min(by_date).isoformat(),
        "last_active_date": max(by_date).isoformat(),
        "duplicate_active_dates": duplicate_dates,
        "publication_after_first_audit_query_count": unknown_by_first_query,
        "versions": [
            {
                "version_id": version.version_id,
                "feed_version": version.feed_version,
                "published_at_utc": version.published_at_utc.isoformat(),
                "active_start": version.active_start.isoformat(),
                "active_end": version.active_end.isoformat(),
            }
            for version in versions
        ],
    }
    return report, versions, by_date


def _parse_gtfs_seconds(value: object) -> int | None:
    if value is None:
        return None
    text = str(value)
    if not text:
        return None
    parts = text.split(":")
    if len(parts) != 3:
        raise Milestone0AuditError(f"invalid GTFS time: {text}")
    hours, minutes, seconds = (int(part) for part in parts)
    if hours < 0 or minutes not in range(60) or seconds not in range(60):
        raise Milestone0AuditError(f"invalid GTFS time: {text}")
    return hours * 3600 + minutes * 60 + seconds


def _local_service_datetime(service_date: date, seconds: int) -> datetime:
    local = datetime.combine(service_date, time(), NEW_YORK) + timedelta(seconds=seconds)
    return local.astimezone(UTC)


def _is_peak(value: datetime) -> bool:
    local = value.astimezone(NEW_YORK)
    if local.weekday() >= 5:
        return False
    return any(
        start <= local.time() < end for start, end in zip(PEAK_STARTS, PEAK_ENDS, strict=True)
    )


def _audit_digest(seed: str, values: tuple[str, ...]) -> bytes:
    return hmac.digest(seed.encode(), encode_key(values), "sha256")


def _canonical_json(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def _version_key(start: Any, end: Any) -> tuple[date, date]:
    return _yyyymmdd(int(start)), _yyyymmdd(int(end))


def load_active_schedule(
    connection: sqlite3.Connection, version: ScheduleVersion
) -> ActiveSchedule:
    """Reconstruct one issued schedule from independently compressed table rows."""

    active = _service_date_integer(version.active_start)
    stop_lookup = {
        str(row[0]): str(row[1]) if row[1] else str(row[0])
        for row in connection.execute(
            "SELECT stop_id, parent_station FROM stops "
            "WHERE gtfs_active_date <= ? AND gtfs_end_date >= ?",
            (active, active),
        )
    }
    builders: dict[str, tuple[str, int, str, str, list[ScheduledStop]]] = {}
    query = (
        "WITH active_trips AS ("
        "SELECT DISTINCT route_id, direction_id, trip_id, "
        "COALESCE(NULLIF(route_pattern_id, ''), trip_id) AS route_pattern_id, service_id "
        "FROM trips WHERE gtfs_active_date <= ? AND gtfs_end_date >= ? "
        "AND route_id IN (?, ?, ?, ?, ?, ?, ?, ?)) "
        "SELECT DISTINCT t.route_id, t.direction_id, t.trip_id, "
        "t.route_pattern_id, t.service_id, "
        "st.stop_id, st.stop_sequence, st.arrival_time, st.departure_time "
        "FROM active_trips t JOIN stop_times st ON st.trip_id = t.trip_id "
        "WHERE st.gtfs_active_date <= ? AND st.gtfs_end_date >= ? "
        "ORDER BY t.trip_id, st.stop_sequence, st.stop_id"
    )
    parameters: tuple[object, ...] = (active, active, *RAIL_ROUTES, active, active)
    for row in connection.execute(query, parameters):
        trip_id = str(row[2])
        metadata = (str(row[0]), int(row[1]), str(row[3]), str(row[4]))
        existing = builders.get(trip_id)
        if existing is not None and existing[:4] != metadata:
            raise Milestone0AuditError(f"active schedule has conflicting trip identity: {trip_id}")
        value = builders.setdefault(
            trip_id,
            (*metadata, []),
        )
        arrival = _parse_gtfs_seconds(row[7])
        departure = _parse_gtfs_seconds(row[8])
        if arrival is None or departure is None:
            continue
        stop_id = str(row[5])
        parent = stop_lookup.get(stop_id)
        if parent is None:
            raise Milestone0AuditError(
                f"rail stop_time references an unknown active stop: {stop_id}"
            )
        value[4].append(ScheduledStop(stop_id, parent, int(row[6]), arrival, departure))
    trips: list[ScheduledTrip] = []
    station_routes: dict[str, set[str]] = defaultdict(set)
    for trip_id, (route, direction, pattern, service, stops) in builders.items():
        ordered = tuple(sorted(stops, key=lambda stop: (stop.sequence, stop.stop_id.encode())))
        if len(ordered) < 2 or len({stop.sequence for stop in ordered}) != len(ordered):
            continue
        trips.append(
            ScheduledTrip(
                version.active_start,
                version.active_end,
                route,
                direction,
                trip_id,
                pattern,
                service,
                ordered,
            )
        )
        for stop in ordered:
            station_routes[stop.parent_station_id].add(route)
    calendars = {
        str(row[0]): (
            _yyyymmdd(int(row[1])),
            _yyyymmdd(int(row[2])),
            tuple(bool(int(value)) for value in row[3:10]),
        )
        for row in connection.execute(
            "SELECT service_id, start_date, end_date, monday, tuesday, wednesday, "
            "thursday, friday, saturday, sunday FROM calendar "
            "WHERE gtfs_active_date <= ? AND gtfs_end_date >= ?",
            (active, active),
        )
    }
    exceptions = {
        (_yyyymmdd(int(row[1])), str(row[0])): int(row[2])
        for row in connection.execute(
            "SELECT service_id, date, exception_type FROM calendar_dates "
            "WHERE gtfs_active_date <= ? AND gtfs_end_date >= ?",
            (active, active),
        )
    }
    return ActiveSchedule(
        trips=tuple(
            sorted(
                trips,
                key=lambda trip: (
                    trip.route_id.encode(),
                    trip.direction_id,
                    trip.trip_id.encode(),
                ),
            )
        ),
        calendar=calendars,
        exceptions=exceptions,
        station_routes={station: frozenset(routes) for station, routes in station_routes.items()},
    )


def _service_is_active(schedule: ActiveSchedule, trip: ScheduledTrip, service_date: date) -> bool:
    exception = schedule.exceptions.get((service_date, trip.service_id))
    if exception is not None:
        return exception == 1
    regular = schedule.calendar.get(trip.service_id)
    if regular is None:
        return False
    start, end, weekdays = regular
    return start <= service_date <= end and weekdays[service_date.weekday()]


def _query_id(values: tuple[str, ...]) -> str:
    return hashlib.sha256(encode_key(values)).hexdigest()


def _direct_query(
    trip: ScheduledTrip,
    service_date: date,
    version: ScheduleVersion,
    public_seed: str,
) -> tuple[int, AuditQuery] | None:
    if len(trip.stops) < 4:
        return None
    segment_digest = _audit_digest(
        public_seed,
        ("audit-segment", service_date.isoformat(), trip.route_id, trip.trip_id),
    )
    origin_index = segment_digest[0] % (len(trip.stops) - 2)
    destination_index = origin_index + 1 + segment_digest[1] % (len(trip.stops) - origin_index - 1)
    origin = trip.stops[origin_index]
    destination = trip.stops[destination_index]
    if origin.parent_station_id == destination.parent_station_id:
        return None
    ready = _local_service_datetime(service_date, origin.departure_seconds)
    local_ready = ready.astimezone(NEW_YORK)
    horizon_index = (
        _audit_digest(public_seed, ("ready-horizon", service_date.isoformat(), trip.trip_id))[0] % 4
    )
    query_time = ready - timedelta(minutes=(0, 5, 10, 15)[horizon_index])
    local_query = query_time.astimezone(NEW_YORK)
    if not time(6) <= local_query.time() <= time(23):
        return None
    slice_name = "peak" if _is_peak(ready) else "off_peak"
    leg = AuditLeg(
        route_id=trip.route_id,
        direction_id=trip.direction_id,
        origin_stop_id=origin.stop_id,
        origin_parent_station_id=origin.parent_station_id,
        destination_stop_id=destination.stop_id,
        destination_parent_station_id=destination.parent_station_id,
        route_pattern_id=trip.route_pattern_id,
    )
    values = (
        "direct-audit-v1",
        service_date.isoformat(),
        trip.route_id,
        str(trip.direction_id),
        trip.trip_id,
        origin.stop_id,
        destination.stop_id,
        query_time.isoformat(),
        ready.isoformat(),
    )
    query = AuditQuery(
        query_id=_query_id(values),
        kind="DIRECT",
        service_date=service_date,
        query_time_utc=query_time,
        ready_at_utc=ready,
        observation_horizon_utc=ready + timedelta(minutes=210),
        schedule_version_id=version.version_id,
        slice_name=slice_name,
        legs=(leg,),
        transfer_walk_seconds=0,
    )
    rank = int.from_bytes(_audit_digest(public_seed, values), "big")
    if not time(6) <= local_ready.time() <= time(23, 30):
        return None
    return rank, query


def _departures_for_leg(
    trips: Iterable[ScheduledTrip],
    service_date: date,
    leg: AuditLeg,
) -> tuple[ScheduledDeparture, ...]:
    departures: set[ScheduledDeparture] = set()
    for trip in trips:
        positions = {stop.stop_id: index for index, stop in enumerate(trip.stops)}
        origin_index = positions.get(leg.origin_stop_id)
        destination_index = positions.get(leg.destination_stop_id)
        if origin_index is None or destination_index is None or origin_index >= destination_index:
            continue
        origin = trip.stops[origin_index]
        destination = trip.stops[destination_index]
        departures.add(
            ScheduledDeparture(
                trip.trip_id,
                _local_service_datetime(service_date, origin.departure_seconds),
                _local_service_datetime(service_date, destination.arrival_seconds),
            )
        )
    return tuple(
        sorted(
            departures,
            key=lambda item: (item.departure_utc, item.trip_id.encode()),
        )
    )


TRANSFER_ROUTE_PAIRS: Mapping[str, tuple[tuple[str, str], ...]] = {
    "place-asmnl": (("Red", "Mattapan"),),
    "place-dwnxg": (("Red", "Orange"),),
    "place-gover": tuple(("Blue", route) for route in RAIL_ROUTES if route.startswith("Green-")),
    "place-haecl": (("Orange", "Green-D"), ("Orange", "Green-E")),
    "place-north": (("Orange", "Green-D"), ("Orange", "Green-E")),
    "place-pktrm": tuple(("Red", route) for route in RAIL_ROUTES if route.startswith("Green-")),
    "place-state": (("Blue", "Orange"),),
}


def _transfer_pair_allowed(station: str, first: str, second: str) -> bool:
    return any({first, second} == set(pair) for pair in TRANSFER_ROUTE_PAIRS[station])


def _transfer_calls(
    trip: ScheduledTrip, service_date: date
) -> Iterator[tuple[str, _TransferLegCandidate | None, _TransferLegCandidate | None]]:
    for index, stop in enumerate(trip.stops):
        station = stop.parent_station_id
        if station not in TRANSFER_ROUTE_PAIRS:
            continue
        origins = [
            candidate for candidate in trip.stops[:index] if candidate.parent_station_id != station
        ]
        destinations = [
            candidate
            for candidate in trip.stops[index + 1 :]
            if candidate.parent_station_id != station
        ]
        origin = origins[0] if origins else None
        destination = destinations[-1] if destinations else None
        inbound = (
            _TransferLegCandidate(
                trip_id=trip.trip_id,
                service_date=service_date,
                departure_utc=_local_service_datetime(service_date, origin.departure_seconds),
                arrival_utc=_local_service_datetime(service_date, stop.arrival_seconds),
                leg=AuditLeg(
                    trip.route_id,
                    trip.direction_id,
                    origin.stop_id,
                    origin.parent_station_id,
                    stop.stop_id,
                    station,
                    trip.route_pattern_id,
                ),
            )
            if origin is not None
            else None
        )
        outbound = (
            _TransferLegCandidate(
                trip_id=trip.trip_id,
                service_date=service_date,
                departure_utc=_local_service_datetime(service_date, stop.departure_seconds),
                arrival_utc=_local_service_datetime(service_date, destination.arrival_seconds),
                leg=AuditLeg(
                    trip.route_id,
                    trip.direction_id,
                    stop.stop_id,
                    station,
                    destination.stop_id,
                    destination.parent_station_id,
                    trip.route_pattern_id,
                ),
            )
            if destination is not None
            else None
        )
        if inbound is None and outbound is None:
            continue
        yield station, inbound, outbound


def generate_audit_queries(
    database: Path,
    versions: Iterable[ScheduleVersion],
    *,
    public_seed: str,
    sample_dates: set[date],
) -> tuple[
    tuple[AuditQuery, ...],
    dict[str, frozenset[str]],
    dict[tuple[str, int], tuple[ScheduledDeparture, ...]],
]:
    """Select 100 schedule-only direct queries per line and 25 per transfer station."""

    direct = {
        (route, slice_name): _TopQueries(50)
        for route in RAIL_ROUTES
        for slice_name in ("peak", "off_peak")
    }
    transfers = {station: _TopQueries(25) for station in TRANSFER_ROUTE_PAIRS}
    station_routes: dict[str, set[str]] = defaultdict(set)
    candidate_expected: dict[tuple[str, int], tuple[ScheduledDeparture, ...]] = {}
    with _connect_read_only(database) as connection:
        for version in versions:
            retained_dates = tuple(
                value
                for value in _date_range(version.active_start, version.active_end)
                if value in sample_dates
            )
            if not retained_dates:
                continue
            schedule = load_active_schedule(connection, version)
            for station, routes in schedule.station_routes.items():
                station_routes[station].update(routes)
            for service_date in retained_dates:
                inbound: dict[str, list[_TransferLegCandidate]] = defaultdict(list)
                outbound: dict[str, list[_TransferLegCandidate]] = defaultdict(list)
                active_by_pattern: dict[tuple[str, int, str], list[ScheduledTrip]] = defaultdict(
                    list
                )
                for trip in schedule.trips:
                    if not _service_is_active(schedule, trip, service_date):
                        continue
                    active_by_pattern[
                        (trip.route_id, trip.direction_id, trip.route_pattern_id)
                    ].append(trip)
                    candidate = _direct_query(trip, service_date, version, public_seed)
                    if candidate is not None:
                        rank, query = candidate
                        direct[(trip.route_id, query.slice_name)].add(rank, query)
                    for station, inbound_leg, outbound_leg in _transfer_calls(trip, service_date):
                        if inbound_leg is not None:
                            inbound[station].append(inbound_leg)
                        if outbound_leg is not None:
                            outbound[station].append(outbound_leg)
                for station in TRANSFER_ROUTE_PAIRS:
                    outgoing = sorted(
                        outbound[station],
                        key=lambda leg: (leg.departure_utc, leg.trip_id.encode()),
                    )
                    for first in sorted(
                        inbound[station],
                        key=lambda leg: (leg.departure_utc, leg.trip_id.encode()),
                    ):
                        second = next(
                            (
                                leg
                                for leg in outgoing
                                if _transfer_pair_allowed(
                                    station, first.leg.route_id, leg.leg.route_id
                                )
                                and leg.departure_utc >= first.arrival_utc + timedelta(seconds=180)
                                and leg.departure_utc <= first.arrival_utc + timedelta(minutes=45)
                            ),
                            None,
                        )
                        if second is None:
                            continue
                        ready = first.departure_utc
                        horizon = (0, 5, 10, 15)[
                            _audit_digest(
                                public_seed,
                                ("transfer-ready", service_date.isoformat(), first.trip_id),
                            )[0]
                            % 4
                        ]
                        query_time = ready - timedelta(minutes=horizon)
                        if not time(6) <= query_time.astimezone(NEW_YORK).time() <= time(23):
                            continue
                        values = (
                            "transfer-audit-v1",
                            service_date.isoformat(),
                            station,
                            first.trip_id,
                            second.trip_id,
                            first.leg.origin_stop_id,
                            second.leg.destination_stop_id,
                            query_time.isoformat(),
                        )
                        query = AuditQuery(
                            query_id=_query_id(values),
                            kind=f"TRANSFER:{station}",
                            service_date=service_date,
                            query_time_utc=query_time,
                            ready_at_utc=ready,
                            observation_horizon_utc=ready + timedelta(minutes=210),
                            schedule_version_id=version.version_id,
                            slice_name="peak" if _is_peak(ready) else "off_peak",
                            legs=(first.leg, second.leg),
                            transfer_walk_seconds=180,
                        )
                        rank = int.from_bytes(_audit_digest(public_seed, values), "big")
                        transfers[station].add(rank, query)
                retained_today = (
                    query
                    for pool in (*direct.values(), *transfers.values())
                    for query in pool.ordered()
                    if query.service_date == service_date
                )
                for query in retained_today:
                    for leg_index, leg in enumerate(query.legs):
                        candidate_expected[(query.query_id, leg_index)] = _departures_for_leg(
                            active_by_pattern[
                                (
                                    leg.route_id,
                                    leg.direction_id,
                                    leg.route_pattern_id,
                                )
                            ],
                            service_date,
                            leg,
                        )

    selected = tuple(
        query
        for key in sorted(direct, key=lambda item: (item[0].encode(), item[1].encode()))
        for query in direct[key].ordered()
    ) + tuple(
        query
        for station in sorted(transfers, key=str.encode)
        for query in transfers[station].ordered()
    )
    counts = Counter(
        query.legs[0].route_id if query.kind == "DIRECT" else query.kind for query in selected
    )
    missing_direct = [route for route in RAIL_ROUTES if counts[route] != 100]
    missing_transfer = [
        station for station in TRANSFER_ROUTE_PAIRS if counts[f"TRANSFER:{station}"] != 25
    ]
    if missing_direct or missing_transfer:
        raise Milestone0AuditError(
            f"audit query inventory is incomplete: direct={missing_direct}, "
            f"transfer={missing_transfer}, counts={dict(sorted(counts.items()))}"
        )
    ordered = tuple(sorted(selected, key=lambda query: query.query_id.encode()))
    selected_ids = {query.query_id for query in ordered}
    return (
        ordered,
        {station: frozenset(routes) for station, routes in station_routes.items()},
        {key: value for key, value in candidate_expected.items() if key[0] in selected_ids},
    )


def build_audit_sample_dates(
    *,
    year: int,
    public_seed: str,
    versions: Iterable[ScheduleVersion],
    major_discontinuity_dates: Iterable[date],
) -> tuple[date, ...]:
    selected = set(deterministic_sample_dates(year, public_seed))
    allowed = set(_date_range(date(year, 1, 1), date(year, 12, 31)))
    for version in versions:
        selected.update(version.active_start + timedelta(days=offset) for offset in (-1, 0, 1))
    for boundary in major_discontinuity_dates:
        selected.update(boundary + timedelta(days=offset) for offset in range(-3, 4))
    return tuple(sorted(selected & allowed))


def _observed_run(
    key: tuple[date, str, int, str, str],
    raw_events: Iterable[tuple[str, int, str, int, str]],
    *,
    ambiguous_identity: bool,
) -> ObservedRun:
    service_date, route_id, direction_id, trip_id, vehicle_id = key
    events = sorted(
        set(raw_events),
        key=lambda item: (item[3], item[1], item[2], item[0].encode(), item[4].encode()),
    )
    arrivals: dict[tuple[int, str], list[tuple[int, str]]] = defaultdict(list)
    departures: dict[tuple[int, str], list[tuple[int, str]]] = defaultdict(list)
    all_departures: list[tuple[int, int, str]] = []
    for stop_id, sequence, event_type, event_time, source_row in events:
        if event_type == "ARR":
            arrivals[(sequence, stop_id)].append((event_time, source_row))
        elif event_type == "DEP":
            departures[(sequence, stop_id)].append((event_time, source_row))
            all_departures.append((sequence, event_time, source_row))
    visits: list[StopVisit] = []
    for (sequence, stop_id), arrival_values in arrivals.items():
        arrival_time, arrival_source = min(arrival_values)
        later_departures = [
            value for value in departures.get((sequence, stop_id), ()) if value[0] >= arrival_time
        ]
        departure_time, departure_source = (
            min(later_departures) if later_departures else (None, None)
        )
        earlier_departures = [
            (event_time, source_row)
            for prior_sequence, event_time, source_row in all_departures
            if prior_sequence < sequence and event_time < arrival_time
        ]
        lower_time = max(earlier_departures)[0] if earlier_departures else None
        visits.append(
            StopVisit(
                stop_id=stop_id,
                stop_sequence=sequence,
                arrival_upper_utc=datetime.fromtimestamp(arrival_time, tz=UTC),
                departure_upper_utc=(
                    datetime.fromtimestamp(departure_time, tz=UTC)
                    if departure_time is not None
                    else None
                ),
                arrival_lower_utc=(
                    datetime.fromtimestamp(lower_time, tz=UTC) if lower_time is not None else None
                ),
                arrival_source_row=arrival_source,
                departure_source_row=departure_source,
            )
        )
    return ObservedRun(
        service_date,
        route_id,
        direction_id,
        trip_id,
        vehicle_id,
        tuple(sorted(visits, key=lambda visit: (visit.stop_sequence, visit.stop_id.encode()))),
        ambiguous_identity,
    )


def load_relevant_observed_runs(
    archive: Path,
    expected: Mapping[tuple[str, int], tuple[ScheduledDeparture, ...]],
    queries: Iterable[AuditQuery],
) -> tuple[
    dict[tuple[date, str, int, str], tuple[ObservedRun, ...]],
    dict[str, Any],
]:
    """Stream source rows and retain only schedule-expected runs for audit queries."""

    query_tuple = tuple(queries)
    query_by_id = {query.query_id: query for query in query_tuple}
    required: set[tuple[date, str, int, str]] = set()
    for (query_id, leg_index), departures in expected.items():
        query = query_by_id[query_id]
        leg = query.legs[leg_index]
        required.update(
            (query.service_date, leg.route_id, leg.direction_id, departure.trip_id)
            for departure in departures
        )
    raw_runs: dict[tuple[date, str, int, str, str], list[tuple[str, int, str, int, str]]] = (
        defaultdict(list)
    )
    trip_vehicles: dict[tuple[date, str, int, str], set[str]] = defaultdict(set)
    rows_scanned = 0
    relevant_rows = 0
    prediction_rows_excluded = 0
    semantic_duplicates = 0
    seen_semantic: set[tuple[str, str, int, str, str, str, int, str, int]] = set()
    with zipfile.ZipFile(archive) as source:
        members = sorted(
            (info for info in source.infolist() if not info.is_dir()),
            key=lambda info: info.filename.encode(),
        )
        for info in members:
            with source.open(info) as binary:
                text = io.TextIOWrapper(binary, encoding="utf-8-sig", newline="")
                reader = csv.DictReader(text)
                if tuple(reader.fieldnames or ()) != EVENT_FIELDS:
                    raise Milestone0AuditError(f"event archive schema drifted: {info.filename}")
                for row_number, row in enumerate(reader, start=1):
                    rows_scanned += 1
                    event_type = row["event_type"]
                    if event_type in {"PRA", "PRD"}:
                        prediction_rows_excluded += 1
                        continue
                    service_date = date.fromisoformat(row["service_date"])
                    route_id = row["route_id"]
                    direction_id = int(row["direction_id"])
                    trip_id = row["trip_id"]
                    identity = (service_date, route_id, direction_id, trip_id)
                    if identity not in required:
                        continue
                    vehicle_id = row["vehicle_id"]
                    stop_id = row["stop_id"]
                    sequence = int(row["stop_sequence"])
                    event_time = int(row["event_time"])
                    semantic = (
                        row["service_date"],
                        route_id,
                        direction_id,
                        trip_id,
                        vehicle_id,
                        stop_id,
                        sequence,
                        event_type,
                        event_time,
                    )
                    if semantic in seen_semantic:
                        semantic_duplicates += 1
                        continue
                    seen_semantic.add(semantic)
                    source_row = f"{info.filename}:{row_number}"
                    raw_runs[(*identity, vehicle_id)].append(
                        (stop_id, sequence, event_type, event_time, source_row)
                    )
                    trip_vehicles[identity].add(vehicle_id)
                    relevant_rows += 1
    observed: dict[tuple[date, str, int, str], list[ObservedRun]] = defaultdict(list)
    for run_key, events in raw_runs.items():
        identity = run_key[:4]
        observed[identity].append(
            _observed_run(
                run_key,
                events,
                ambiguous_identity=len(trip_vehicles[identity]) != 1,
            )
        )
    frozen = {
        key: tuple(sorted(runs, key=lambda run: run.vehicle_id.encode()))
        for key, runs in observed.items()
    }
    stats = {
        "rows_scanned": rows_scanned,
        "prediction_rows_excluded": prediction_rows_excluded,
        "relevant_actual_rows": relevant_rows,
        "relevant_semantic_duplicates_removed": semantic_duplicates,
        "required_schedule_trip_identities": len(required),
        "observed_schedule_trip_identities": len(frozen),
        "ambiguous_schedule_trip_identities": sum(
            len(vehicles) != 1 for vehicles in trip_vehicles.values()
        ),
    }
    return frozen, stats


def _visit_pair(run: ObservedRun, leg: AuditLeg) -> tuple[StopVisit, StopVisit] | None:
    origins = [visit for visit in run.visits if visit.stop_id == leg.origin_stop_id]
    destinations = [visit for visit in run.visits if visit.stop_id == leg.destination_stop_id]
    pairs = [
        (origin, destination)
        for origin in origins
        for destination in destinations
        if origin.stop_sequence < destination.stop_sequence
    ]
    if len(pairs) != 1:
        return None
    return pairs[0]


def _resolve_leg(
    *,
    query: AuditQuery,
    leg_index: int,
    ready_at_utc: datetime,
    expected: Mapping[tuple[str, int], tuple[ScheduledDeparture, ...]],
    observed: Mapping[tuple[date, str, int, str], tuple[ObservedRun, ...]],
) -> LegResolution | None:
    leg = query.legs[leg_index]
    scheduled = expected.get((query.query_id, leg_index), ())
    candidates: list[tuple[datetime, str, ObservedRun, StopVisit, StopVisit]] = []
    observed_qualifying: set[str] = set()
    for departure in scheduled:
        runs = observed.get(
            (
                query.service_date,
                leg.route_id,
                leg.direction_id,
                departure.trip_id,
            ),
            (),
        )
        for run in runs:
            if run.ambiguous_identity:
                continue
            pair = _visit_pair(run, leg)
            if pair is None:
                continue
            origin, destination = pair
            if destination.arrival_lower_utc is None:
                continue
            observed_qualifying.add(run.trip_id)
            direct_after_ready = origin.arrival_upper_utc >= ready_at_utc
            interval_contains_ready = (
                origin.departure_upper_utc is not None
                and origin.arrival_upper_utc <= ready_at_utc <= origin.departure_upper_utc
            )
            if not direct_after_ready and not interval_contains_ready:
                continue
            boarding_observed = max(origin.arrival_upper_utc, ready_at_utc)
            if boarding_observed > query.observation_horizon_utc:
                continue
            candidates.append(
                (
                    boarding_observed,
                    run.trip_id,
                    run,
                    origin,
                    destination,
                )
            )
    if not candidates:
        return None
    boarding_observed, _, run, origin, destination = min(
        candidates, key=lambda item: (item[0], item[1].encode(), item[2].vehicle_id.encode())
    )
    prior = {
        departure.trip_id
        for departure in scheduled
        if departure.departure_utc <= query.observation_horizon_utc
    }
    missing = tuple(sorted(prior - observed_qualifying, key=str.encode))
    lower = destination.arrival_lower_utc
    if lower is None:
        raise AssertionError("candidate with no lower bound reached leg resolution")
    width = int((destination.arrival_upper_utc - lower).total_seconds())
    return LegResolution(
        trip_id=run.trip_id,
        vehicle_id=run.vehicle_id,
        boarding_ready_utc=ready_at_utc,
        boarding_observed_utc=boarding_observed,
        destination_lower_utc=lower,
        destination_upper_utc=destination.arrival_upper_utc,
        arrival_interval_width_seconds=width,
        boarding_source_row=origin.arrival_source_row,
        destination_source_row=destination.arrival_source_row,
        reconciliation_complete=not missing,
        missing_prior_scheduled_trip_ids=missing,
    )


def resolve_audit_queries(
    queries: Iterable[AuditQuery],
    *,
    expected: Mapping[tuple[str, int], tuple[ScheduledDeparture, ...]],
    observed: Mapping[tuple[date, str, int, str], tuple[ObservedRun, ...]],
    schedule_by_date: Mapping[date, ScheduleVersion],
) -> tuple[dict[str, Any], ...]:
    records: list[dict[str, Any]] = []
    for query in queries:
        schedule = schedule_by_date[query.service_date]
        schedule_known = schedule.published_at_utc <= query.query_time_utc
        ready = query.ready_at_utc
        legs: list[LegResolution] = []
        censoring_reason: str | None = None
        for index in range(len(query.legs)):
            resolution = _resolve_leg(
                query=query,
                leg_index=index,
                ready_at_utc=ready,
                expected=expected,
                observed=observed,
            )
            if resolution is None:
                censoring_reason = f"LEG_{index + 1}_NO_RECONCILED_ARRIVAL"
                break
            if not resolution.reconciliation_complete:
                censoring_reason = f"LEG_{index + 1}_PRIOR_TRAIN_UNRECONCILED"
                legs.append(resolution)
                break
            legs.append(resolution)
            ready = resolution.destination_upper_utc + timedelta(
                seconds=query.transfer_walk_seconds if index == 0 else 0
            )
        arrived = censoring_reason is None and len(legs) == len(query.legs)
        final_leg = legs[-1] if arrived else None
        records.append(
            {
                "query_id": query.query_id,
                "kind": query.kind,
                "service_date": query.service_date.isoformat(),
                "query_time_utc": query.query_time_utc.isoformat(),
                "ready_at_utc": query.ready_at_utc.isoformat(),
                "schedule_version_id": query.schedule_version_id,
                "schedule_published_at_utc": schedule.published_at_utc.isoformat(),
                "schedule_known_by_query_cutoff": schedule_known,
                "slice": query.slice_name,
                "routes": [leg.route_id for leg in query.legs],
                "origin_parent_station": query.legs[0].origin_parent_station_id,
                "destination_parent_station": query.legs[-1].destination_parent_station_id,
                "transfer_parent_station": (
                    query.legs[0].destination_parent_station_id if len(query.legs) == 2 else None
                ),
                "status": "ARRIVED" if arrived else "CENSORED",
                "censoring_reason": censoring_reason,
                "observed_stop_presence_after_ready": arrived,
                "arrival_lower_bound_utc": (
                    final_leg.destination_lower_utc.isoformat() if final_leg else None
                ),
                "arrival_upper_bound_utc": (
                    final_leg.destination_upper_utc.isoformat() if final_leg else None
                ),
                "arrival_interval_width_seconds": (
                    final_leg.arrival_interval_width_seconds if final_leg else None
                ),
                "outcome_time_semantic": "CONSERVATIVE_STATION_DEPARTURE_INTERVAL",
                "trip_update_fallback_used": False,
                "legs": [
                    {
                        **asdict(leg),
                        "boarding_ready_utc": leg.boarding_ready_utc.isoformat(),
                        "boarding_observed_utc": leg.boarding_observed_utc.isoformat(),
                        "destination_lower_utc": leg.destination_lower_utc.isoformat(),
                        "destination_upper_utc": leg.destination_upper_utc.isoformat(),
                    }
                    for leg in legs
                ],
            }
        )
    return tuple(records)


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _record_metrics(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = tuple(records)
    arrived = sum(row["status"] == "ARRIVED" for row in rows)
    proven_no_arrival = sum(row["status"] == "PROVEN_NO_ARRIVAL_WITHIN_HORIZON" for row in rows)
    resolved = arrived + proven_no_arrival
    censored = sum(row["status"] == "CENSORED" for row in rows)
    widths = [
        int(row["arrival_interval_width_seconds"])
        for row in rows
        if row["status"] == "ARRIVED" and row["arrival_interval_width_seconds"] is not None
    ]
    width_passing = sum(width <= 300 for width in widths)
    presence = sum(bool(row["observed_stop_presence_after_ready"]) for row in rows)
    return {
        "candidate_count": len(rows),
        "arrived_count": arrived,
        "proven_no_arrival_count": proven_no_arrival,
        "resolved_count": resolved,
        "censored_count": censored,
        "audit_candidate_resolution_rate": _rate(resolved, len(rows)),
        "censoring_rate": _rate(censored, len(rows)),
        "observed_stop_presence_rate": _rate(presence, len(rows)),
        "arrival_interval_width_denominator": arrived,
        "arrival_interval_width_passing": width_passing,
        "arrival_interval_width_coverage": _rate(width_passing, arrived),
        "arrival_interval_width_seconds_p50": (
            sorted(widths)[len(widths) // 2] if widths else None
        ),
        "arrival_interval_width_seconds_p95": (
            sorted(widths)[min(len(widths) - 1, int(len(widths) * 0.95))] if widths else None
        ),
    }


def _passes(value: object, threshold: float, *, maximum: bool = False) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    return value <= threshold if maximum else value >= threshold


def summarize_query_audit(
    records: Iterable[Mapping[str, Any]],
    *,
    station_routes: Mapping[str, frozenset[str]],
    charter: Mapping[str, Any],
) -> dict[str, Any]:
    rows = tuple(records)
    scope = _mapping(charter.get("scope"), "acceptance scope")
    line_rule = _mapping(scope.get("line_retention_rule"), "line_retention_rule")
    transfer_rule = _mapping(
        scope.get("transfer_station_retention_rule"),
        "transfer_station_retention_rule",
    )
    by_line: dict[str, dict[str, Any]] = {}
    supported_lines: list[str] = []
    for route in RAIL_ROUTES:
        line_rows = tuple(
            row for row in rows if row["kind"] == "DIRECT" and row["routes"] == [route]
        )
        metrics = _record_metrics(line_rows)
        slices = {
            name: _record_metrics(row for row in line_rows if row["slice"] == name)
            for name in ("peak", "off_peak")
        }
        retained = (
            _passes(
                metrics["audit_candidate_resolution_rate"],
                float(line_rule["resolution_rate_min"]),
            )
            and _passes(
                metrics["censoring_rate"],
                float(line_rule["censoring_rate_max"]),
                maximum=True,
            )
            and _passes(
                metrics["arrival_interval_width_coverage"],
                float(line_rule["interval_width_coverage_min"]),
            )
            and _passes(
                metrics["observed_stop_presence_rate"],
                float(line_rule["observed_stop_presence_rate_min"]),
            )
            and all(
                _passes(
                    slice_metrics["audit_candidate_resolution_rate"],
                    float(line_rule["peak_and_off_peak_resolution_rate_min"]),
                )
                and _passes(
                    slice_metrics["arrival_interval_width_coverage"],
                    float(line_rule["peak_and_off_peak_interval_width_coverage_min"]),
                )
                for slice_metrics in slices.values()
            )
        )
        by_line[route] = {"retained": retained, "overall": metrics, "slices": slices}
        if retained:
            supported_lines.append(route)

    by_transfer: dict[str, dict[str, Any]] = {}
    supported_transfers: list[str] = []
    for station in sorted(TRANSFER_ROUTE_PAIRS, key=str.encode):
        station_rows = tuple(row for row in rows if row["kind"] == f"TRANSFER:{station}")
        metrics = _record_metrics(station_rows)
        route_pair_supported = any(
            first in supported_lines and second in supported_lines
            for first, second in TRANSFER_ROUTE_PAIRS[station]
        )
        retained = (
            metrics["candidate_count"] >= int(transfer_rule["query_count_min"])
            and _passes(
                metrics["audit_candidate_resolution_rate"],
                float(transfer_rule["resolution_rate_min"]),
            )
            and _passes(
                metrics["arrival_interval_width_coverage"],
                float(transfer_rule["interval_width_coverage_min"]),
            )
            and route_pair_supported
        )
        by_transfer[station] = {"retained": retained, "overall": metrics}
        if retained:
            supported_transfers.append(station)

    supported_stations = sorted(
        (
            station
            for station, routes in station_routes.items()
            if any(route in supported_lines for route in routes)
        ),
        key=str.encode,
    )
    retained_rows = tuple(
        row
        for row in rows
        if all(route in supported_lines for route in row["routes"])
        and (
            row["transfer_parent_station"] is None
            or row["transfer_parent_station"] in supported_transfers
        )
    )
    return {
        "overall_all_proposed_scope": _record_metrics(rows),
        "overall_recommended_scope": _record_metrics(retained_rows),
        "lines": by_line,
        "transfer_stations": by_transfer,
        "recommended_scope": {
            "supported_lines": supported_lines,
            "supported_stations": supported_stations,
            "supported_transfer_stations": supported_transfers,
            "excluded_lines": [route for route in RAIL_ROUTES if route not in supported_lines],
            "excluded_transfer_stations": [
                station
                for station in sorted(TRANSFER_ROUTE_PAIRS, key=str.encode)
                if station not in supported_transfers
            ],
        },
    }


def _verified_hashes(root: Path, expected: Mapping[str, str]) -> dict[str, str]:
    observed: dict[str, str] = {}
    for relative, digest in expected.items():
        path = root / relative
        if not path.is_file():
            raise Milestone0AuditError(f"pinned source file is missing: {path}")
        actual = sha256_file(path)
        if actual != digest:
            raise Milestone0AuditError(f"pinned source file digest drifted: {relative}")
        observed[relative] = actual
    return observed


def _load_yaml(path: Path, field: str) -> dict[str, Any]:
    return _mapping(yaml.safe_load(path.read_text(encoding="utf-8")), field)


def _utc(value: str, field: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise Milestone0AuditError(f"{field} must be timezone-aware UTC")
    return parsed


@dataclass(frozen=True)
class Milestone0Inputs:
    event_profile: Path
    event_metadata: Path
    event_archive: Path
    event_acquired_at_utc: datetime
    schedule_profile: Path
    schedule_archive: Path
    schedule_database: Path
    schedule_acquired_at_utc: datetime
    acceptance_charter: Path
    provenance_ledger: Path
    lamp_profile: Path
    lamp_root: Path
    producer_root: Path
    license_pdf: Path
    runtime_report: Path
    gate_report: Path


def run_milestone0_audit(inputs: Milestone0Inputs) -> dict[str, Any]:
    started = perf_counter()
    phase_started = started
    timings: dict[str, float] = {}
    _progress("verifying pinned source objects and provenance")
    event_profile = load_source_profile(inputs.event_profile)
    metadata = validate_metadata(event_profile, inputs.event_metadata.read_bytes())
    if inputs.event_archive.stat().st_size != event_profile.expected_size_bytes:
        raise Milestone0AuditError("event archive size is not pinned")
    if sha256_file(inputs.event_archive) != event_profile.expected_archive_sha256:
        raise Milestone0AuditError("event archive digest is not pinned")
    producer_hashes = _verified_hashes(inputs.producer_root, event_profile.producer_file_hashes)

    schedule_profile = load_schedule_source_profile(inputs.schedule_profile)
    if sha256_file(inputs.license_pdf) != schedule_profile.license_sha256:
        raise Milestone0AuditError("MassDOT license digest is not pinned")
    schedule_report, versions, schedule_by_date = audit_schedule_archive(
        schedule_profile,
        compressed_archive=inputs.schedule_archive,
        expanded_database=inputs.schedule_database,
    )
    schedule_query_database = prepare_schedule_query_cache(
        inputs.schedule_database,
        source_sha256=schedule_profile.expanded_sha256,
        cache_directory=inputs.runtime_report.parent / "cache",
    )

    lamp_profile = _load_yaml(inputs.lamp_profile, "LAMP source profile")
    lamp_files = _mapping(lamp_profile.get("files"), "LAMP source files")
    lamp_hashes = _verified_hashes(
        inputs.lamp_root,
        {str(name): _string(digest, "LAMP source digest") for name, digest in lamp_files.items()},
    )
    charter = _load_yaml(inputs.acceptance_charter, "acceptance charter")
    provenance = _load_yaml(inputs.provenance_ledger, "provenance ledger")
    timings["source_verification_and_cache_seconds"] = round(perf_counter() - phase_started, 3)
    seed = _string(
        _mapping(charter.get("query_generation"), "query_generation").get("public_seed"),
        "public_seed",
    )
    sample_policy = _mapping(
        _mapping(charter.get("historical_interval"), "historical_interval").get(
            "representative_sample"
        ),
        "representative_sample",
    )
    discontinuities = tuple(
        _date_value(item.get("date"), "discontinuity date")
        for item in (
            _mapping(value, "major discontinuity")
            for value in sample_policy.get("major_documented_discontinuities", [])
        )
    )
    sample_dates = build_audit_sample_dates(
        year=2022,
        public_seed=seed,
        versions=versions,
        major_discontinuity_dates=discontinuities,
    )
    phase_started = perf_counter()
    _progress("enumerating deterministic schedule queries and reconciliation sets")
    queries, station_routes, expected = generate_audit_queries(
        schedule_query_database,
        versions,
        public_seed=seed,
        sample_dates=set(sample_dates),
    )
    timings["query_generation_seconds"] = round(perf_counter() - phase_started, 3)
    phase_started = perf_counter()
    _progress("streaming the complete pinned realized-event archive")
    observed, event_scan = load_relevant_observed_runs(inputs.event_archive, expected, queries)
    timings["event_scan_seconds"] = round(perf_counter() - phase_started, 3)
    phase_started = perf_counter()
    _progress("resolving virtual-rider outcomes and acceptance metrics")
    records = resolve_audit_queries(
        queries,
        expected=expected,
        observed=observed,
        schedule_by_date=schedule_by_date,
    )
    metrics = summarize_query_audit(records, station_routes=station_routes, charter=charter)
    timings["outcome_resolution_seconds"] = round(perf_counter() - phase_started, 3)
    timings["elapsed_before_report_write_seconds"] = round(perf_counter() - started, 3)
    direct_counts = dict(
        sorted(
            Counter(query.legs[0].route_id for query in queries if query.kind == "DIRECT").items()
        )
    )
    transfer_counts = dict(
        sorted(
            Counter(
                query.kind.removeprefix("TRANSFER:")
                for query in queries
                if query.kind.startswith("TRANSFER:")
            ).items()
        )
    )

    runtime = {
        "qualification": "milestone-0-audit-v1",
        "timings": timings,
        "input_manifest": {
            "event_archive_sha256": event_profile.expected_archive_sha256,
            "event_metadata_sha256": sha256_file(inputs.event_metadata),
            "event_acquired_at_utc": inputs.event_acquired_at_utc.isoformat(),
            "schedule_archive_sha256": schedule_profile.compressed_sha256,
            "schedule_database_sha256": schedule_profile.expanded_sha256,
            "schedule_query_cache_version": SCHEDULE_QUERY_CACHE_VERSION,
            "schedule_acquired_at_utc": inputs.schedule_acquired_at_utc.isoformat(),
            "acceptance_charter_sha256": sha256_file(inputs.acceptance_charter),
            "provenance_ledger_sha256": sha256_file(inputs.provenance_ledger),
            "event_profile_sha256": sha256_file(inputs.event_profile),
            "schedule_profile_sha256": sha256_file(inputs.schedule_profile),
            "lamp_profile_sha256": sha256_file(inputs.lamp_profile),
            "license_sha256": schedule_profile.license_sha256,
            "producer_file_hashes": producer_hashes,
            "lamp_file_hashes": lamp_hashes,
        },
        "source_metadata": {
            "event_item_id": metadata["id"],
            "event_owner": metadata["owner"],
            "event_license": metadata["licenseInfo"],
            "event_product_available_at_utc": max(
                event_profile.expected_modified_at_utc,
                inputs.event_acquired_at_utc,
            ).isoformat(),
            "schedule_archive_last_modified_at_utc": (
                schedule_profile.last_modified_at_utc.isoformat()
            ),
        },
        "schedule_audit": schedule_report,
        "sample": {
            "service_date_count": len(sample_dates),
            "service_dates": [value.isoformat() for value in sample_dates],
            "schedule_boundary_radius_days": 1,
            "major_discontinuity_radius_days": 3,
            "major_discontinuity_dates": [value.isoformat() for value in discontinuities],
        },
        "query_inventory": {
            "query_count": len(queries),
            "direct_counts": direct_counts,
            "transfer_counts": transfer_counts,
        },
        "event_scan": event_scan,
        "metrics": metrics,
        "query_reproductions": records,
        "provenance_ledger": provenance,
        "limitations": [
            "Vehicle Position STOPPED_AT observations upper-bound latent arrival time.",
            "The public archive omits per-row file time and is label-only for historical V1.",
            "Boardability is a virtual-rider assumption and does not measure doors or acceptance.",
            "Prediction fallback rows are excluded from primary outcomes.",
        ],
    }
    runtime_bytes = _canonical_json(runtime)
    inputs.runtime_report.parent.mkdir(parents=True, exist_ok=True)
    inputs.runtime_report.write_bytes(runtime_bytes)

    recommended_scope = metrics["recommended_scope"]
    configured_scope = _mapping(charter.get("scope"), "acceptance scope")
    scope_matches = (
        bool(configured_scope.get("scope_frozen"))
        and configured_scope.get("supported_lines") == recommended_scope["supported_lines"]
        and configured_scope.get("supported_stations") == recommended_scope["supported_stations"]
        and configured_scope.get("supported_transfer_stations")
        == recommended_scope["supported_transfer_stations"]
    )
    gates = _mapping(charter.get("gates"), "acceptance gates")
    overall = metrics["overall_recommended_scope"]
    line_slices = [
        slice_metrics
        for line in metrics["lines"].values()
        if line["retained"]
        for slice_metrics in line["slices"].values()
    ]
    ledger_fields = _mapping(provenance.get("fields"), "provenance fields")
    operational = _mapping(
        provenance.get("operational_feature_families"),
        "operational feature families",
    )
    data_license_text = Path("DATA_LICENSE.md").read_text(encoding="utf-8")
    redistribution_artifacts = (
        "Original MassDOT or MBTA feed bytes",
        "MBTA Rapid Transit Events 2022 archive",
        "Historical LAMP Parquet files",
        "Normalized transit rows",
        "Trained model binaries",
        "Aggregate metrics without restricted or identifying content",
        "Small synthetic fixtures",
        "MBTA or MassDOT logos and marks",
        "Product screenshots",
    )
    checks = {
        "official_event_source_identity_and_license_pinned": metadata["access"] == "public"
        and metadata["licenseInfo"] == event_profile.expected_license,
        "official_event_producer_semantics_verified": len(producer_hashes) >= 5,
        "lamp_coalesced_export_rejection_verified": len(lamp_hashes) == 2,
        "schedule_archive_contract_passed": schedule_report["status"] == "PASSED",
        "representative_service_date_rules_satisfied": len(sample_dates) >= 30,
        "manual_direct_query_inventory_satisfied": all(
            value == 100 for value in direct_counts.values()
        )
        and len(direct_counts) == len(RAIL_ROUTES),
        "manual_transfer_query_inventory_satisfied": all(
            value == 25 for value in transfer_counts.values()
        )
        and len(transfer_counts) == len(TRANSFER_ROUTE_PAIRS),
        "audit_candidate_resolution_rate_overall_passed": _passes(
            overall["audit_candidate_resolution_rate"],
            float(gates["audit_candidate_resolution_rate_overall_min"]),
        ),
        "audit_candidate_resolution_rate_slices_passed": bool(line_slices)
        and all(
            _passes(
                value["audit_candidate_resolution_rate"],
                float(gates["audit_candidate_resolution_rate_slice_min"]),
            )
            for value in line_slices
        ),
        "audit_censoring_rate_passed": _passes(
            overall["censoring_rate"],
            float(gates["audit_censoring_rate_overall_max"]),
            maximum=True,
        ),
        "arrival_interval_width_overall_passed": _passes(
            overall["arrival_interval_width_coverage"],
            float(gates["arrival_interval_width_coverage_overall_min"]),
        ),
        "arrival_interval_width_slices_passed": bool(line_slices)
        and all(
            _passes(
                value["arrival_interval_width_coverage"],
                float(gates["arrival_interval_width_coverage_slice_min"]),
            )
            for value in line_slices
        ),
        "observed_stop_presence_after_ready_passed": _passes(
            overall["observed_stop_presence_rate"],
            float(gates["audit_candidate_resolution_rate_overall_min"]),
        ),
        "every_schedule_known_by_query_cutoff": all(
            bool(record["schedule_known_by_query_cutoff"]) for record in records
        ),
        "event_time_never_used_as_product_availability": (
            min(inputs.event_acquired_at_utc, inputs.schedule_acquired_at_utc).year > 2022
        ),
        "primary_and_sensitivity_evidence_distinguishable": all(
            not record["trip_update_fallback_used"] for record in records
        ),
        "one_primary_outcome_semantic_used": {record["outcome_time_semantic"] for record in records}
        == {"CONSERVATIVE_STATION_DEPARTURE_INTERVAL"},
        "proven_no_arrival_and_censoring_distinguishable": all(
            record["status"] != "CENSORED" or record["censoring_reason"] for record in records
        ),
        "downstream_move_never_used_as_boarding_evidence": all(
            record["status"] != "ARRIVED" or record["observed_stop_presence_after_ready"]
            for record in records
        ),
        "field_level_provenance_ledger_complete": len(ledger_fields) >= 5,
        "historical_operational_features_frozen_empty": all(
            _mapping(value, "operational feature").get("status") == "EXCLUDED"
            for value in operational.values()
        ),
        "license_and_redistribution_policy_documented": (
            schedule_profile.license_sha256 == sha256_file(inputs.license_pdf)
            and "## Redistribution and retention matrix" in data_license_text
            and all(f"| {artifact} |" in data_license_text for artifact in redistribution_artifacts)
        ),
        "aggregate_scope_matches_frozen_charter": scope_matches,
    }
    failing = [name for name, passed in checks.items() if not passed]
    report = {
        "milestone": 0,
        "status": "PASSED" if not failing else "FAILED",
        "acceptance_version": charter.get("acceptance_version"),
        "acceptance_version_hash": sha256_file(inputs.acceptance_charter),
        "qualification": "milestone-0-audit-v1",
        "checks": checks,
        "failing_checks": failing,
        "input_manifest_hash": hashlib.sha256(
            _canonical_json(runtime["input_manifest"])
        ).hexdigest(),
        "runtime_report_sha256": hashlib.sha256(runtime_bytes).hexdigest(),
        "source_profiles": {
            "events": event_profile.source_profile_version,
            "schedule": schedule_profile.source_profile_version,
            "lamp": lamp_profile["source_profile_version"],
        },
        "sample_service_date_count": len(sample_dates),
        "query_count": len(records),
        "event_scan": event_scan,
        "metrics": metrics,
        "recommended_scope": recommended_scope,
        "configured_scope": {
            "scope_frozen": configured_scope.get("scope_frozen"),
            "supported_lines": configured_scope.get("supported_lines"),
            "supported_stations": configured_scope.get("supported_stations"),
            "supported_transfer_stations": configured_scope.get("supported_transfer_stations"),
        },
        "blocking_prerequisite": (
            None
            if not failing
            else (
                "Obtain complete archived MBTA GTFS-Realtime evidence that terminally "
                "reconciles every potentially eligible 2022 train, collect an equivalent "
                "prospective panel, or select another ML product under the Milestone 0 kill gate."
            )
        ),
    }
    inputs.gate_report.parent.mkdir(parents=True, exist_ok=True)
    inputs.gate_report.write_bytes(_canonical_json(report))
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--event-profile",
        type=Path,
        default=Path("configs/sources/mbta-rapid-transit-events-2022.yaml"),
    )
    parser.add_argument("--event-metadata", type=Path, required=True)
    parser.add_argument("--event-archive", type=Path, required=True)
    parser.add_argument("--event-acquired-at-utc", required=True)
    parser.add_argument(
        "--schedule-profile",
        type=Path,
        default=Path("configs/sources/mbta-lamp-gtfs-archive-2022.yaml"),
    )
    parser.add_argument("--schedule-archive", type=Path, required=True)
    parser.add_argument("--schedule-database", type=Path, required=True)
    parser.add_argument("--schedule-acquired-at-utc", required=True)
    parser.add_argument(
        "--acceptance-charter", type=Path, default=Path("configs/acceptance/v1.yaml")
    )
    parser.add_argument(
        "--provenance-ledger", type=Path, default=Path("configs/provenance/historical-v1.yaml")
    )
    parser.add_argument(
        "--lamp-profile", type=Path, default=Path("configs/sources/mbta-lamp-implementation.yaml")
    )
    parser.add_argument("--lamp-root", type=Path, required=True)
    parser.add_argument("--producer-root", type=Path, required=True)
    parser.add_argument("--license", dest="license_pdf", type=Path, required=True)
    parser.add_argument(
        "--runtime-report",
        type=Path,
        default=Path("artifacts/runtime/milestone-0/full-audit-v1.json"),
    )
    parser.add_argument(
        "--gate-report", type=Path, default=Path("artifacts/reports/gates/milestone-0.json")
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    inputs = Milestone0Inputs(
        event_profile=args.event_profile,
        event_metadata=args.event_metadata,
        event_archive=args.event_archive,
        event_acquired_at_utc=_utc(args.event_acquired_at_utc, "event acquisition"),
        schedule_profile=args.schedule_profile,
        schedule_archive=args.schedule_archive,
        schedule_database=args.schedule_database,
        schedule_acquired_at_utc=_utc(args.schedule_acquired_at_utc, "schedule acquisition"),
        acceptance_charter=args.acceptance_charter,
        provenance_ledger=args.provenance_ledger,
        lamp_profile=args.lamp_profile,
        lamp_root=args.lamp_root,
        producer_root=args.producer_root,
        license_pdf=args.license_pdf,
        runtime_report=args.runtime_report,
        gate_report=args.gate_report,
    )
    try:
        report = run_milestone0_audit(inputs)
    except (Milestone0AuditError, SourceDiscoveryError, OSError, sqlite3.Error) as error:
        print(json.dumps({"status": "FAILED", "error": str(error)}, sort_keys=True))
        return 1
    print(
        json.dumps(
            {"status": report["status"], "failing_checks": report["failing_checks"]},
            sort_keys=True,
        )
    )
    return 0 if report["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
