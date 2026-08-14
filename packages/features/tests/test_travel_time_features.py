from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest
from arrive90_data_contracts.travel_time import (
    EpisodeScheduleMatchStatus,
    HistoricalVehicleStatus,
    SourceLineageEntry,
    TripScheduleRelationship,
    VehicleObservation,
    vehicle_observation_id,
)
from arrive90_features.travel_time import (
    FutureObservationAccessError,
    ObservationCutoffView,
    TravelTimeFeatureRow,
    build_travel_time_feature_row,
)
from arrive90_ingestion.episodes import build_trip_episodes
from arrive90_ingestion.historical_schedule import (
    EpisodeScheduleMatch,
    ScheduledStop,
    ScheduledTrip,
    ScheduleMatchReason,
)

SERVICE_DATE = date(2024, 5, 15)
START = datetime(2024, 5, 15, 12, tzinfo=UTC)


def _observation(
    seconds: int,
    sequence: int,
    status: HistoricalVehicleStatus,
) -> VehicleObservation:
    observed = START + timedelta(seconds=seconds)
    identifier = vehicle_observation_id(
        trip_start_date=SERVICE_DATE,
        trip_start_time="08:00:00",
        trip_id="trip-1",
        route_id="Red",
        direction_id=0,
        vehicle_id="vehicle-1",
        observation_utc=observed,
        stop_sequence=sequence,
        current_status=status,
    )
    return VehicleObservation(
        observation_id=identifier,
        source_lineage=(SourceLineageEntry("day.parquet", seconds),),
        entity_id="entity",
        trip_id="trip-1",
        trip_start_date=SERVICE_DATE,
        trip_start_time="08:00:00",
        schedule_relationship=TripScheduleRelationship.SCHEDULED,
        route_id="Red",
        direction_id=0,
        vehicle_id="vehicle-1",
        vehicle_label="train",
        observation_source_naive_utc=observed.replace(tzinfo=None),
        observation_utc=observed,
        stop_sequence=sequence,
        stop_id=f"stop-{sequence}",
        current_status=status,
        latitude=42.36,
        longitude=-71.06,
        bearing=None,
        speed=5.0,
        schema_version="test-v1",
    )


def _stop(sequence: int, seconds: int) -> ScheduledStop:
    observed = START + timedelta(seconds=seconds)
    return ScheduledStop(
        stop_id=f"stop-{sequence}",
        stop_sequence=sequence,
        arrival_local_seconds=8 * 3600 + seconds,
        departure_local_seconds=8 * 3600 + seconds,
        arrival_utc=observed,
        departure_utc=observed,
    )


def _fixture() -> tuple[
    EpisodeScheduleMatch,
    dict[str, VehicleObservation],
    VehicleObservation,
    VehicleObservation,
]:
    prior = _observation(0, 1, HistoricalVehicleStatus.STOPPED_AT)
    incoming = _observation(60, 10, HistoricalVehicleStatus.INCOMING_AT)
    anchor = _observation(120, 10, HistoricalVehicleStatus.STOPPED_AT)
    future = _observation(180, 20, HistoricalVehicleStatus.STOPPED_AT)
    observations = [prior, incoming, anchor, future]
    episode = build_trip_episodes(observations).episodes[0]
    episode = replace(
        episode,
        schedule_match_status=EpisodeScheduleMatchStatus.EXACT_MATCH,
        schedule_version_id="schedule-v1",
        route_pattern_id="Red-1-0",
    )
    trip = ScheduledTrip(
        schedule_version_id="schedule-v1",
        feed_version="Spring, 2024-05-14T19:00:00+00:00, A",
        published_at_utc=datetime(2024, 5, 14, 19, tzinfo=UTC),
        service_date=SERVICE_DATE,
        service_id="weekday",
        trip_id="trip-1",
        route_id="Red",
        direction_id=0,
        route_pattern_id="Red-1-0",
        trip_start_time="08:00:00",
        stops=(_stop(1, 0), _stop(10, 300), _stop(20, 600)),
    )
    match = EpisodeScheduleMatch(episode, ScheduleMatchReason.EXACT, trip)
    return match, {item.observation_id: item for item in observations}, anchor, future


def test_cutoff_feature_row_uses_only_available_observations() -> None:
    match, observations, anchor, future = _fixture()
    view = ObservationCutoffView.from_episode(
        match.episode,
        observations,
        cutoff_utc=anchor.observation_utc,
    )
    first = build_travel_time_feature_row(
        match,
        view,
        anchor_observation_id=anchor.observation_id,
        destination_stop_id="stop-20",
        destination_stop_sequence=20,
        destination_offset=1,
        scheduled_remaining_seconds=300,
    )
    second = build_travel_time_feature_row(
        match,
        view,
        anchor_observation_id=anchor.observation_id,
        destination_stop_id="stop-20",
        destination_stop_sequence=20,
        destination_offset=1,
        scheduled_remaining_seconds=300,
    )
    assert first == second
    assert future.observation_id not in first.source_observation_ids
    values = dict(first.values)
    assert values["elapsed_episode_seconds"] == 120
    assert values["observed_stops_before_anchor"] == 1
    assert values["previous_stopped_segment_seconds"] == 120
    assert values["median_last_three_segment_seconds"] == 120
    assert values["most_recent_observation_gap_seconds"] == 60
    assert values["anchor_bearing_missing"] is True
    assert values["anchor_speed_missing"] is False


def test_cutoff_view_rejects_direct_future_observation_access() -> None:
    match, observations, anchor, future = _fixture()
    view = ObservationCutoffView.from_episode(
        match.episode,
        observations,
        cutoff_utc=anchor.observation_utc,
    )
    assert view.observation(anchor.observation_id) == anchor
    with pytest.raises(FutureObservationAccessError, match="after the feature cutoff"):
        view.observation(future.observation_id)


def test_feature_builder_rejects_future_schedule_and_mismatched_destination() -> None:
    match, observations, anchor, _ = _fixture()
    view = ObservationCutoffView.from_episode(
        match.episode,
        observations,
        cutoff_utc=anchor.observation_utc,
    )
    assert match.scheduled_trip is not None
    future_trip = replace(
        match.scheduled_trip,
        published_at_utc=anchor.observation_utc + timedelta(seconds=1),
    )
    with pytest.raises(FutureObservationAccessError, match="schedule version"):
        build_travel_time_feature_row(
            replace(match, scheduled_trip=future_trip),
            view,
            anchor_observation_id=anchor.observation_id,
            destination_stop_id="stop-20",
            destination_stop_sequence=20,
            destination_offset=1,
            scheduled_remaining_seconds=300,
        )
    with pytest.raises(ValueError, match="destination offset"):
        build_travel_time_feature_row(
            match,
            view,
            anchor_observation_id=anchor.observation_id,
            destination_stop_id="stop-20",
            destination_stop_sequence=20,
            destination_offset=2,
            scheduled_remaining_seconds=300,
        )


def test_cutoff_view_and_row_contracts_fail_closed() -> None:
    match, observations, anchor, future = _fixture()
    with pytest.raises(ValueError, match="every and only"):
        ObservationCutoffView(
            match.episode,
            anchor.observation_utc,
            tuple(item for item in observations.values() if item != future),
        )
    with pytest.raises(ValueError, match="bytewise sorted"):
        TravelTimeFeatureRow(
            anchor_observation_id=anchor.observation_id,
            feature_cutoff_utc=anchor.observation_utc,
            schedule_version_id="schedule-v1",
            route_pattern_id="Red-1-0",
            feature_schema_version="v1",
            values=(("z", 1), ("a", 2)),
            source_observation_ids=(anchor.observation_id,),
            source_lineage_keys=("day.parquet:1",),
        )
