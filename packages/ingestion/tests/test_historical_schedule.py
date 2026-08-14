from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from arrive90_data_contracts.travel_time import (
    EpisodeScheduleMatchStatus,
    HistoricalVehicleStatus,
    SourceLineageEntry,
    TripScheduleRelationship,
    VehicleObservation,
    vehicle_observation_id,
)
from arrive90_ingestion.episodes import build_trip_episodes
from arrive90_ingestion.historical_schedule import (
    ScheduleMatchReason,
    ScheduleMatchResult,
    load_schedule_day,
    match_episodes_to_schedule,
)

SERVICE_DATE = date(2024, 5, 15)
OBSERVED = datetime(2024, 5, 15, 12, tzinfo=UTC)
DATABASE_SHA256 = "a" * 64


def _database(
    path: Path,
    *,
    published_at: str = "2024-05-14T19:00:00+00:00",
    duplicate_version: bool = False,
    duplicate_trip: bool = False,
) -> Path:
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
            """
        )
        feed_version = f"Spring, {published_at}, A"
        connection.execute("INSERT INTO feed_info VALUES (?, 20240515, 20240515)", (feed_version,))
        if duplicate_version:
            connection.execute(
                "INSERT INTO feed_info VALUES (?, 20240515, 20240515)",
                (f"Spring, {published_at}, B",),
            )
        connection.execute(
            "INSERT INTO calendar VALUES "
            "('weekday', 1, 1, 1, 1, 1, 0, 0, 20240101, 20241231, "
            "20240101, 20241231)"
        )
        connection.execute(
            "INSERT INTO route_patterns VALUES ('Red-1-0', 'Red', 0, 20240101, 20241231)"
        )
        connection.execute(
            "INSERT INTO trips VALUES "
            "('Red', 'weekday', 'trip-1', 0, 'Red-1-0', 20240101, 20241231)"
        )
        if duplicate_trip:
            connection.execute(
                "INSERT INTO trips VALUES "
                "('Red', 'weekday', 'trip-1', 0, 'Red-1-0', 20240101, 20241231)"
            )
        connection.executemany(
            "INSERT INTO stop_times VALUES (?, ?, ?, ?, ?, 20240101, 20241231)",
            [
                ("trip-1", "08:00:00", "08:00:00", "stop-1", 1),
                ("trip-1", "08:05:00", "08:05:00", "stop-10", 10),
                ("trip-1", "08:10:00", "08:10:00", "stop-20", 20),
            ],
        )
    return path


def _observation(
    seconds: int,
    sequence: int | None,
    stop_id: str | None,
    *,
    trip_id: str = "trip-1",
    route_id: str = "Red",
    start_time: str = "08:00:00",
    relationship: TripScheduleRelationship = TripScheduleRelationship.SCHEDULED,
) -> VehicleObservation:
    observed = OBSERVED + timedelta(seconds=seconds)
    identifier = vehicle_observation_id(
        trip_start_date=SERVICE_DATE,
        trip_start_time=start_time,
        trip_id=trip_id,
        route_id=route_id,
        direction_id=0,
        vehicle_id="vehicle-1",
        observation_utc=observed,
        stop_sequence=sequence,
        current_status=HistoricalVehicleStatus.STOPPED_AT,
    )
    return VehicleObservation(
        observation_id=identifier,
        source_lineage=(SourceLineageEntry("day.parquet", seconds),),
        entity_id="entity",
        trip_id=trip_id,
        trip_start_date=SERVICE_DATE,
        trip_start_time=start_time,
        schedule_relationship=relationship,
        route_id=route_id,
        direction_id=0,
        vehicle_id="vehicle-1",
        vehicle_label="train",
        observation_source_naive_utc=observed.replace(tzinfo=None),
        observation_utc=observed,
        stop_sequence=sequence,
        stop_id=stop_id,
        current_status=HistoricalVehicleStatus.STOPPED_AT,
        latitude=None,
        longitude=None,
        bearing=None,
        speed=None,
        schema_version="test-v1",
    )


def _match(path: Path, observations: list[VehicleObservation]) -> ScheduleMatchResult:
    episodes = build_trip_episodes(observations).episodes
    return match_episodes_to_schedule(
        path,
        expanded_database_sha256=DATABASE_SHA256,
        episodes=episodes,
        observations_by_id={item.observation_id: item for item in observations},
    )


def test_schedule_day_and_episode_match_are_exact_and_deterministic(tmp_path: Path) -> None:
    database = _database(tmp_path / "schedule.db")
    observations = [
        _observation(0, 1, "stop-1"),
        _observation(60, 10, "stop-10"),
        _observation(120, None, None),
    ]
    day = load_schedule_day(
        database,
        service_date=SERVICE_DATE,
        cutoff_utc=OBSERVED,
        expanded_database_sha256=DATABASE_SHA256,
    )
    assert len(day.trips) == 1
    assert day.trips[0].trip_start_time == "08:00:00"
    assert day.trips[0].stops[0].arrival_utc == OBSERVED
    assert day.trips[0].published_at_utc < OBSERVED

    first = _match(database, observations)
    second = _match(database, list(reversed(observations)))
    assert first == second
    assert first.matched_episode_count == 1
    assert first.reason_counts == (("EXACT", 1),)
    match = first.matches[0]
    assert match.episode.schedule_match_status is EpisodeScheduleMatchStatus.EXACT_MATCH
    assert match.episode.schedule_version_id == day.version.schedule_version_id
    assert match.episode.route_pattern_id == "Red-1-0"
    assert match.scheduled_trip == day.trips[0]


@pytest.mark.parametrize(
    ("observations", "reason"),
    [
        (
            [_observation(0, 1, "wrong-stop"), _observation(60, 10, "stop-10")],
            ScheduleMatchReason.PLATFORM_SEQUENCE_MISMATCH,
        ),
        (
            [_observation(0, 1, None), _observation(60, 10, "stop-10")],
            ScheduleMatchReason.PLATFORM_SEQUENCE_MISMATCH,
        ),
        (
            [
                _observation(0, 1, "stop-1", start_time="08:01:00"),
                _observation(60, 10, "stop-10", start_time="08:01:00"),
            ],
            ScheduleMatchReason.START_TIME_MISMATCH,
        ),
        (
            [
                _observation(
                    0,
                    1,
                    "stop-1",
                    relationship=TripScheduleRelationship.ADDED,
                ),
                _observation(
                    60,
                    10,
                    "stop-10",
                    relationship=TripScheduleRelationship.ADDED,
                ),
            ],
            ScheduleMatchReason.NON_SCHEDULED_RELATIONSHIP,
        ),
        (
            [
                _observation(0, 1, "stop-1", route_id="Orange"),
                _observation(60, 10, "stop-10", route_id="Orange"),
            ],
            ScheduleMatchReason.ROUTE_DIRECTION_MISMATCH,
        ),
    ],
)
def test_schedule_match_rejects_every_exact_identity_mismatch(
    tmp_path: Path,
    observations: list[VehicleObservation],
    reason: ScheduleMatchReason,
) -> None:
    result = _match(_database(tmp_path / "schedule.db"), observations)
    assert result.matches[0].reason is reason
    assert result.matches[0].scheduled_trip is None
    assert (
        result.matches[0].episode.schedule_match_status
        is EpisodeScheduleMatchStatus.SCHEDULE_UNMATCHED
    )


def test_schedule_match_reports_duplicate_trip_identity(tmp_path: Path) -> None:
    result = _match(
        _database(tmp_path / "schedule.db", duplicate_trip=True),
        [_observation(0, 1, "stop-1"), _observation(60, 10, "stop-10")],
    )
    assert result.matches[0].reason is ScheduleMatchReason.TRIP_ID_CONFLICT


@pytest.mark.parametrize(
    "database_options",
    [
        {"published_at": "2024-05-15T13:00:00+00:00"},
        {"duplicate_version": True},
    ],
)
def test_future_or_conflicting_schedule_version_fails_closed(
    tmp_path: Path, database_options: dict[str, object]
) -> None:
    database = _database(tmp_path / "schedule.db", **database_options)  # type: ignore[arg-type]
    result = _match(
        database,
        [_observation(0, 1, "stop-1"), _observation(60, 10, "stop-10")],
    )
    assert result.schedule_days == ()
    assert result.matches[0].reason is ScheduleMatchReason.FUTURE_OR_INVALID_VERSION
    assert (
        result.matches[0].episode.schedule_match_status
        is EpisodeScheduleMatchStatus.SCHEDULE_VERSION_CONFLICT
    )


def test_schedule_match_reports_unknown_trip_without_borrowing(tmp_path: Path) -> None:
    database = _database(tmp_path / "schedule.db")
    observations = [
        _observation(0, 1, "stop-1", trip_id="unknown"),
        _observation(60, 10, "stop-10", trip_id="unknown"),
    ]
    result = _match(database, observations)
    assert result.matches[0].reason is ScheduleMatchReason.TRIP_NOT_FOUND
