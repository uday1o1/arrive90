from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

from arrive90_data_contracts.travel_time import (
    DownstreamOutcomeState,
    EpisodeScheduleMatchStatus,
    HistoricalVehicleStatus,
    SourceLineageEntry,
    TripScheduleRelationship,
    VehicleObservation,
    vehicle_observation_id,
)
from arrive90_ingestion.episodes import build_trip_episodes
from arrive90_ingestion.historical_schedule import (
    EpisodeScheduleMatch,
    ScheduledStop,
    ScheduledTrip,
    ScheduleMatchReason,
)
from arrive90_outcomes.travel_time import TargetBuildResult, build_downstream_examples

SERVICE_DATE = date(2024, 5, 15)
START = datetime(2024, 5, 15, 12, tzinfo=UTC)


def _observation(
    seconds: int,
    sequence: int,
    status: HistoricalVehicleStatus,
    *,
    stop_id: str | None = None,
) -> VehicleObservation:
    observed = START + timedelta(seconds=seconds)
    resolved_stop_id = stop_id if stop_id is not None else f"stop-{sequence}"
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
        source_lineage=(SourceLineageEntry("day.parquet", seconds * 10 + sequence),),
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
        stop_id=resolved_stop_id,
        current_status=status,
        latitude=None,
        longitude=None,
        bearing=None,
        speed=None,
        schema_version="test-v1",
    )


def _stop(sequence: int, scheduled_seconds: int) -> ScheduledStop:
    observed = START + timedelta(seconds=scheduled_seconds)
    local_seconds = 8 * 3600 + scheduled_seconds
    return ScheduledStop(
        stop_id=f"stop-{sequence}",
        stop_sequence=sequence,
        arrival_local_seconds=local_seconds,
        departure_local_seconds=local_seconds,
        arrival_utc=observed,
        departure_utc=observed,
    )


def _trip(stops: tuple[ScheduledStop, ...]) -> ScheduledTrip:
    return ScheduledTrip(
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
        stops=stops,
    )


def _matches(
    observations: list[VehicleObservation],
    *,
    stops: tuple[ScheduledStop, ...] | None = None,
    exact: bool = True,
) -> tuple[tuple[EpisodeScheduleMatch, ...], dict[str, VehicleObservation]]:
    episodes = build_trip_episodes(observations).episodes
    scheduled_trip = _trip(stops or (_stop(1, 0), _stop(10, 300)))
    matches = tuple(
        EpisodeScheduleMatch(
            episode=(
                replace(
                    episode,
                    schedule_match_status=EpisodeScheduleMatchStatus.EXACT_MATCH,
                    schedule_version_id="schedule-v1",
                    route_pattern_id="Red-1-0",
                )
                if exact
                else episode
            ),
            reason=(ScheduleMatchReason.EXACT if exact else ScheduleMatchReason.TRIP_NOT_FOUND),
            scheduled_trip=scheduled_trip if exact else None,
        )
        for episode in episodes
    )
    return matches, {item.observation_id: item for item in observations}


def _build(
    observations: list[VehicleObservation],
    *,
    stops: tuple[ScheduledStop, ...] | None = None,
    exact: bool = True,
) -> TargetBuildResult:
    matches, by_id = _matches(observations, stops=stops, exact=exact)
    return build_downstream_examples(matches, by_id)


def test_interval_resolved_uses_latest_strictly_earlier_lower_evidence() -> None:
    anchor = _observation(0, 1, HistoricalVehicleStatus.STOPPED_AT)
    lower = _observation(60, 10, HistoricalVehicleStatus.INCOMING_AT)
    upper = _observation(120, 10, HistoricalVehicleStatus.STOPPED_AT)
    result = _build([upper, anchor, lower])
    assert len(result.examples) == 1
    example = result.examples[0]
    assert example.outcome_state is DownstreamOutcomeState.INTERVAL_RESOLVED
    assert example.lower_evidence_observation_id == lower.observation_id
    assert example.upper_evidence_observation_id == upper.observation_id
    assert (example.lower_bound_seconds, example.upper_bound_seconds) == (60, 120)


def test_anchor_only_lower_evidence_is_left_censored() -> None:
    anchor = _observation(0, 1, HistoricalVehicleStatus.STOPPED_AT)
    upper = _observation(120, 10, HistoricalVehicleStatus.STOPPED_AT)
    example = _build([anchor, upper]).examples[0]
    assert example.outcome_state is DownstreamOutcomeState.LEFT_CENSORED
    assert example.lower_evidence_observation_id == anchor.observation_id
    assert (example.lower_bound_seconds, example.upper_bound_seconds) == (0, 120)


def test_same_timestamp_destination_status_cannot_supply_lower_evidence() -> None:
    anchor = _observation(0, 1, HistoricalVehicleStatus.STOPPED_AT)
    incoming = _observation(120, 10, HistoricalVehicleStatus.INCOMING_AT)
    stopped = _observation(120, 10, HistoricalVehicleStatus.STOPPED_AT)
    example = _build([anchor, incoming, stopped]).examples[0]
    assert example.outcome_state is DownstreamOutcomeState.LEFT_CENSORED
    assert example.lower_evidence_observation_id == anchor.observation_id
    assert example.upper_evidence_observation_id == stopped.observation_id


def test_over_width_finite_interval_remains_visible_but_ineligible() -> None:
    example = _build(
        [
            _observation(0, 1, HistoricalVehicleStatus.STOPPED_AT),
            _observation(181, 10, HistoricalVehicleStatus.STOPPED_AT),
        ]
    ).examples[0]
    assert example.outcome_state is DownstreamOutcomeState.OVER_WIDTH_INTERVAL
    assert example.upper_bound_seconds == 181
    assert not example.included_in_likelihood


def test_unresolved_destination_becomes_positive_right_censored() -> None:
    follow_up = _observation(100, 10, HistoricalVehicleStatus.INCOMING_AT)
    example = _build([_observation(0, 1, HistoricalVehicleStatus.STOPPED_AT), follow_up]).examples[
        0
    ]
    assert example.outcome_state is DownstreamOutcomeState.RIGHT_CENSORED
    assert example.lower_evidence_observation_id == follow_up.observation_id
    assert example.lower_bound_seconds == 100
    assert example.upper_bound_seconds == math.inf


def test_destination_without_positive_follow_up_is_explicit() -> None:
    example = _build([_observation(0, 1, HistoricalVehicleStatus.STOPPED_AT)]).examples[0]
    assert example.outcome_state is DownstreamOutcomeState.NO_FOLLOW_UP
    assert example.lower_bound_seconds is None


def test_later_sequence_without_destination_stop_is_missing_observation() -> None:
    stops = (_stop(1, 0), _stop(10, 300), _stop(20, 600))
    result = _build(
        [
            _observation(0, 1, HistoricalVehicleStatus.STOPPED_AT),
            _observation(120, 20, HistoricalVehicleStatus.STOPPED_AT),
        ],
        stops=stops,
    )
    by_destination = {example.destination_stop_sequence: example for example in result.examples}
    assert by_destination[10].outcome_state is DownstreamOutcomeState.MISSING_STOP_OBSERVATION
    assert by_destination[10].upper_bound_seconds is None
    assert by_destination[20].outcome_state is DownstreamOutcomeState.LEFT_CENSORED


def test_episode_boundary_prevents_cross_episode_arrival_borrowing() -> None:
    stops = (_stop(1, 0), _stop(10, 300))
    observations = [
        _observation(0, 1, HistoricalVehicleStatus.STOPPED_AT),
        _observation(601, 10, HistoricalVehicleStatus.STOPPED_AT),
    ]
    matches, by_id = _matches(observations, stops=stops)
    result = build_downstream_examples(matches, by_id)
    first = next(
        example
        for example in result.examples
        if example.anchor_observation_id == observations[0].observation_id
    )
    assert first.outcome_state is DownstreamOutcomeState.SESSION_DISCONTINUITY
    assert first.upper_evidence_observation_id is None


def test_base_weights_sum_to_one_for_each_anchor() -> None:
    stops = (_stop(1, 0), _stop(10, 300), _stop(20, 600), _stop(30, 900))
    result = _build(
        [
            _observation(0, 1, HistoricalVehicleStatus.STOPPED_AT),
            _observation(60, 10, HistoricalVehicleStatus.STOPPED_AT),
            _observation(120, 20, HistoricalVehicleStatus.STOPPED_AT),
            _observation(180, 30, HistoricalVehicleStatus.STOPPED_AT),
        ],
        stops=stops,
    )
    by_anchor: dict[str, float] = defaultdict(float)
    for example in result.examples:
        by_anchor[example.anchor_observation_id] += example.base_weight
    assert by_anchor
    assert all(math.isclose(weight, 1.0) for weight in by_anchor.values())


def test_unmatched_schedule_keeps_anchor_in_population_without_destination() -> None:
    result = _build([_observation(0, 1, HistoricalVehicleStatus.STOPPED_AT)], exact=False)
    assert result.anchor_count == 1
    assert result.matched_anchor_count == 0
    assert len(result.examples) == 1
    assert result.examples[0].outcome_state is DownstreamOutcomeState.SCHEDULE_UNMATCHED
    assert result.examples[0].destination_stop_id is None


def test_ambiguous_same_timestamp_sequences_are_not_prediction_anchors() -> None:
    stops = (_stop(1, 0), _stop(10, 300), _stop(20, 600))
    result = _build(
        [
            _observation(0, 1, HistoricalVehicleStatus.STOPPED_AT),
            _observation(0, 10, HistoricalVehicleStatus.STOPPED_AT),
            _observation(60, 20, HistoricalVehicleStatus.STOPPED_AT),
        ],
        stops=stops,
    )
    assert result.anchor_count == 1
    assert result.terminal_anchor_count == 1
    assert result.examples == ()
