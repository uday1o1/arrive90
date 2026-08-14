"""Exact read-only matching against the official historical MBTA schedule database."""

from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from itertools import pairwise
from pathlib import Path
from zoneinfo import ZoneInfo

from arrive90_data_contracts.travel_time import (
    EpisodeScheduleMatchStatus,
    TripEpisode,
    TripScheduleRelationship,
    VehicleObservation,
)

from arrive90_ingestion.acquisition import (
    AcquisitionError,
    ScheduleVersion,
    select_schedule_version,
)

BOSTON = ZoneInfo("America/New_York")
HEAVY_RAIL_ROUTES = ("Blue", "Orange", "Red")


class HistoricalScheduleError(ValueError):
    """Raised when the historical schedule cannot satisfy the frozen contract."""


class ScheduleMatchReason(StrEnum):
    """Complete deterministic reason vocabulary for episode schedule matching."""

    EXACT = "EXACT"
    FUTURE_OR_INVALID_VERSION = "FUTURE_OR_INVALID_VERSION"
    TRIP_NOT_FOUND = "TRIP_NOT_FOUND"
    TRIP_ID_CONFLICT = "TRIP_ID_CONFLICT"
    ROUTE_DIRECTION_MISMATCH = "ROUTE_DIRECTION_MISMATCH"
    START_TIME_MISMATCH = "START_TIME_MISMATCH"
    NON_SCHEDULED_RELATIONSHIP = "NON_SCHEDULED_RELATIONSHIP"
    PLATFORM_SEQUENCE_MISMATCH = "PLATFORM_SEQUENCE_MISMATCH"


@dataclass(frozen=True, slots=True)
class ScheduledStop:
    """One exact platform and sequence in an active historical trip."""

    stop_id: str
    stop_sequence: int
    arrival_local_seconds: int
    departure_local_seconds: int
    arrival_utc: datetime
    departure_utc: datetime


@dataclass(frozen=True, slots=True)
class ScheduledTrip:
    """One service-date trip from one content-addressed schedule version."""

    schedule_version_id: str
    feed_version: str
    published_at_utc: datetime
    service_date: date
    service_id: str
    trip_id: str
    route_id: str
    direction_id: int
    route_pattern_id: str
    trip_start_time: str
    stops: tuple[ScheduledStop, ...]


@dataclass(frozen=True, slots=True)
class ScheduleDay:
    """Active service and trip rows for one service date and schedule version."""

    version: ScheduleVersion
    service_date: date
    trips: tuple[ScheduledTrip, ...]

    def trip_index(self) -> dict[str, tuple[ScheduledTrip, ...]]:
        """Return every trip candidate without hiding duplicate identifiers."""

        indexed: dict[str, list[ScheduledTrip]] = defaultdict(list)
        for trip in self.trips:
            indexed[trip.trip_id].append(trip)
        return {trip_id: tuple(values) for trip_id, values in indexed.items()}


@dataclass(frozen=True, slots=True)
class EpisodeScheduleMatch:
    """One episode plus its exact matched trip, if any."""

    episode: TripEpisode
    reason: ScheduleMatchReason
    scheduled_trip: ScheduledTrip | None


@dataclass(frozen=True, slots=True)
class ScheduleMatchResult:
    """Deterministically ordered schedule days and per-episode match results."""

    schedule_days: tuple[ScheduleDay, ...]
    matches: tuple[EpisodeScheduleMatch, ...]
    reason_counts: tuple[tuple[str, int], ...]

    @property
    def matched_episode_count(self) -> int:
        """Return the number of exact episode matches."""

        return sum(match.reason is ScheduleMatchReason.EXACT for match in self.matches)


def _connect_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _gtfs_seconds(value: object, field: str) -> int:
    text = str(value)
    pieces = text.split(":")
    if len(pieces) != 3 or not all(piece.isdigit() for piece in pieces):
        raise HistoricalScheduleError(f"{field} must use GTFS HH:MM:SS format")
    hours, minutes, seconds = (int(piece) for piece in pieces)
    if hours < 0 or not 0 <= minutes < 60 or not 0 <= seconds < 60:
        raise HistoricalScheduleError(f"{field} must use GTFS HH:MM:SS format")
    return hours * 3600 + minutes * 60 + seconds


def _gtfs_time_text(seconds: int) -> str:
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _scheduled_utc(service_date: date, local_seconds: int) -> datetime:
    local = datetime.combine(service_date, time(), tzinfo=BOSTON) + timedelta(seconds=local_seconds)
    return local.astimezone(UTC)


def _active_services(connection: sqlite3.Connection, service_date: date) -> frozenset[str]:
    service_key = int(service_date.strftime("%Y%m%d"))
    weekday = (
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    )[service_date.weekday()]
    rows = connection.execute(
        f"SELECT service_id FROM calendar WHERE start_date <= ? AND end_date >= ? "  # noqa: S608 - weekday is selected from a closed tuple.
        f"AND {weekday} = 1 AND gtfs_active_date <= ? AND gtfs_end_date >= ?",
        (service_key, service_key, service_key, service_key),
    ).fetchall()
    active = {str(row["service_id"]) for row in rows}
    exceptions = connection.execute(
        "SELECT service_id, exception_type FROM calendar_dates WHERE date = ? "
        "AND gtfs_active_date <= ? AND gtfs_end_date >= ?",
        (service_key, service_key, service_key),
    ).fetchall()
    for row in exceptions:
        service_id = str(row["service_id"])
        exception_type = int(row["exception_type"])
        if exception_type == 1:
            active.add(service_id)
        elif exception_type == 2:
            active.discard(service_id)
        else:
            raise HistoricalScheduleError(
                f"unknown calendar exception type for {service_id}: {exception_type}"
            )
    if not active:
        raise HistoricalScheduleError(f"schedule has no active service for {service_date}")
    return frozenset(active)


def _route_patterns(
    connection: sqlite3.Connection, service_date: date
) -> dict[str, tuple[str, int]]:
    service_key = int(service_date.strftime("%Y%m%d"))
    rows = connection.execute(
        "SELECT route_pattern_id, route_id, direction_id FROM route_patterns "
        "WHERE gtfs_active_date <= ? AND gtfs_end_date >= ? "
        "AND route_id IN ('Blue', 'Orange', 'Red')",
        (service_key, service_key),
    ).fetchall()
    patterns: dict[str, tuple[str, int]] = {}
    for row in rows:
        pattern_id = str(row["route_pattern_id"])
        value = (str(row["route_id"]), int(row["direction_id"]))
        if pattern_id in patterns and patterns[pattern_id] != value:
            raise HistoricalScheduleError(f"active route pattern is conflicting: {pattern_id}")
        patterns[pattern_id] = value
    return patterns


def load_schedule_day(
    database: Path,
    *,
    service_date: date,
    cutoff_utc: datetime,
    expanded_database_sha256: str,
) -> ScheduleDay:
    """Load one exact historical service date through a read-only SQLite connection."""

    try:
        version = select_schedule_version(
            database,
            service_date=service_date,
            cutoff_utc=cutoff_utc,
            expanded_database_sha256=expanded_database_sha256,
        )
        with _connect_read_only(database) as connection:
            active_services = _active_services(connection, service_date)
            patterns = _route_patterns(connection, service_date)
            service_key = int(service_date.strftime("%Y%m%d"))
            trip_rows = connection.execute(
                "SELECT trip_id, route_id, service_id, direction_id, route_pattern_id "
                "FROM trips WHERE gtfs_active_date <= ? AND gtfs_end_date >= ? "
                "AND route_id IN ('Blue', 'Orange', 'Red')",
                (service_key, service_key),
            ).fetchall()
            stop_rows = connection.execute(
                "SELECT stop_times.trip_id, stop_times.arrival_time, "
                "stop_times.departure_time, stop_times.stop_id, "
                "stop_times.stop_sequence FROM stop_times JOIN "
                "(SELECT DISTINCT trip_id FROM trips WHERE gtfs_active_date <= ? "
                "AND gtfs_end_date >= ? AND route_id IN ('Blue', 'Orange', 'Red')) "
                "AS active_trips ON active_trips.trip_id = stop_times.trip_id "
                "WHERE stop_times.gtfs_active_date <= ? AND stop_times.gtfs_end_date >= ?",
                (service_key, service_key, service_key, service_key),
            ).fetchall()
    except sqlite3.Error as error:
        raise HistoricalScheduleError("historical schedule schema or query is invalid") from error

    stops_by_trip: dict[str, list[ScheduledStop]] = defaultdict(list)
    for row in stop_rows:
        arrival_seconds = _gtfs_seconds(row["arrival_time"], "arrival_time")
        departure_seconds = _gtfs_seconds(row["departure_time"], "departure_time")
        if departure_seconds < arrival_seconds:
            raise HistoricalScheduleError("scheduled departure cannot precede arrival")
        stops_by_trip[str(row["trip_id"])].append(
            ScheduledStop(
                stop_id=str(row["stop_id"]),
                stop_sequence=int(row["stop_sequence"]),
                arrival_local_seconds=arrival_seconds,
                departure_local_seconds=departure_seconds,
                arrival_utc=_scheduled_utc(service_date, arrival_seconds),
                departure_utc=_scheduled_utc(service_date, departure_seconds),
            )
        )

    trips: list[ScheduledTrip] = []
    for row in trip_rows:
        service_id = str(row["service_id"])
        if service_id not in active_services:
            continue
        trip_id = str(row["trip_id"])
        route_id = str(row["route_id"])
        direction_id = int(row["direction_id"])
        pattern_id = str(row["route_pattern_id"])
        if not trip_id or not route_id or not service_id or not pattern_id:
            raise HistoricalScheduleError("active schedule trip has an empty identity field")
        if patterns.get(pattern_id) != (route_id, direction_id):
            raise HistoricalScheduleError(
                f"trip route pattern is not active for its route and direction: {trip_id}"
            )
        stops = tuple(
            sorted(
                stops_by_trip.get(trip_id, []),
                key=lambda stop: (stop.stop_sequence, stop.stop_id.encode()),
            )
        )
        if not stops:
            raise HistoricalScheduleError(f"active trip has no stop times: {trip_id}")
        if len({stop.stop_sequence for stop in stops}) != len(stops):
            raise HistoricalScheduleError(f"active trip has duplicate stop sequences: {trip_id}")
        if any(current.arrival_utc < previous.arrival_utc for previous, current in pairwise(stops)):
            raise HistoricalScheduleError(f"active trip schedule time regresses: {trip_id}")
        trips.append(
            ScheduledTrip(
                schedule_version_id=version.schedule_version_id,
                feed_version=version.feed_version,
                published_at_utc=version.published_at_utc,
                service_date=service_date,
                service_id=service_id,
                trip_id=trip_id,
                route_id=route_id,
                direction_id=direction_id,
                route_pattern_id=pattern_id,
                trip_start_time=_gtfs_time_text(stops[0].departure_local_seconds),
                stops=stops,
            )
        )
    trips.sort(
        key=lambda trip: (
            trip.trip_id.encode(),
            trip.route_id.encode(),
            trip.direction_id,
            trip.route_pattern_id.encode(),
        )
    )
    return ScheduleDay(version=version, service_date=service_date, trips=tuple(trips))


def _match_episode(
    episode: TripEpisode,
    observations_by_id: dict[str, VehicleObservation],
    trip_index: dict[str, tuple[ScheduledTrip, ...]],
) -> EpisodeScheduleMatch:
    observations = [observations_by_id[identifier] for identifier in episode.observation_ids]
    if any(
        observation.schedule_relationship is not TripScheduleRelationship.SCHEDULED
        for observation in observations
    ):
        return EpisodeScheduleMatch(episode, ScheduleMatchReason.NON_SCHEDULED_RELATIONSHIP, None)
    candidates = trip_index.get(episode.trip_id, ())
    if not candidates:
        return EpisodeScheduleMatch(episode, ScheduleMatchReason.TRIP_NOT_FOUND, None)
    if len(candidates) != 1:
        return EpisodeScheduleMatch(episode, ScheduleMatchReason.TRIP_ID_CONFLICT, None)
    trip = candidates[0]
    if (trip.route_id, trip.direction_id) != (episode.route_id, episode.direction_id):
        return EpisodeScheduleMatch(episode, ScheduleMatchReason.ROUTE_DIRECTION_MISMATCH, None)
    if trip.trip_start_time != episode.trip_start_time:
        return EpisodeScheduleMatch(episode, ScheduleMatchReason.START_TIME_MISMATCH, None)
    stops = {stop.stop_sequence: stop.stop_id for stop in trip.stops}
    for observation in observations:
        if observation.stop_sequence is None and observation.stop_id is None:
            continue
        if (
            observation.stop_sequence is None
            or observation.stop_id is None
            or stops.get(observation.stop_sequence) != observation.stop_id
        ):
            return EpisodeScheduleMatch(
                episode, ScheduleMatchReason.PLATFORM_SEQUENCE_MISMATCH, None
            )
    matched = replace(
        episode,
        schedule_match_status=EpisodeScheduleMatchStatus.EXACT_MATCH,
        schedule_version_id=trip.schedule_version_id,
        route_pattern_id=trip.route_pattern_id,
    )
    return EpisodeScheduleMatch(matched, ScheduleMatchReason.EXACT, trip)


def match_episodes_to_schedule(
    database: Path,
    *,
    expanded_database_sha256: str,
    episodes: tuple[TripEpisode, ...],
    observations_by_id: dict[str, VehicleObservation],
) -> ScheduleMatchResult:
    """Match episodes to exact service-date schedule rows available by their cutoff."""

    episodes_by_date: dict[date, list[TripEpisode]] = defaultdict(list)
    for episode in episodes:
        episodes_by_date[episode.service_date].append(episode)

    days: list[ScheduleDay] = []
    matches: list[EpisodeScheduleMatch] = []
    for service_date in sorted(episodes_by_date):
        dated_episodes = episodes_by_date[service_date]
        earliest_cutoff = min(episode.first_observation_utc for episode in dated_episodes)
        try:
            day = load_schedule_day(
                database,
                service_date=service_date,
                cutoff_utc=earliest_cutoff,
                expanded_database_sha256=expanded_database_sha256,
            )
        except AcquisitionError:
            matches.extend(
                EpisodeScheduleMatch(
                    replace(
                        episode,
                        schedule_match_status=EpisodeScheduleMatchStatus.SCHEDULE_VERSION_CONFLICT,
                        schedule_version_id=None,
                        route_pattern_id=None,
                    ),
                    ScheduleMatchReason.FUTURE_OR_INVALID_VERSION,
                    None,
                )
                for episode in dated_episodes
            )
            continue
        days.append(day)
        trip_index = day.trip_index()
        matches.extend(
            _match_episode(episode, observations_by_id, trip_index) for episode in dated_episodes
        )

    matches.sort(
        key=lambda match: (
            match.episode.service_date,
            match.episode.trip_start_time.encode(),
            match.episode.trip_id.encode(),
            match.episode.episode_id,
        )
    )
    reason_counts = Counter(match.reason.value for match in matches)
    return ScheduleMatchResult(
        schedule_days=tuple(days),
        matches=tuple(matches),
        reason_counts=tuple(sorted(reason_counts.items(), key=lambda item: item[0].encode())),
    )
