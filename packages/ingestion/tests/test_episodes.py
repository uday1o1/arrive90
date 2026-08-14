from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from arrive90_data_contracts.travel_time import (
    EpisodeQualityFlag,
    HistoricalVehicleStatus,
    SourceLineageEntry,
    TripScheduleRelationship,
    VehicleObservation,
    vehicle_observation_id,
)
from arrive90_ingestion.episodes import build_trip_episodes, stopped_sequences

START = datetime(2024, 5, 15, 12, tzinfo=UTC)


def _observation(
    seconds: int,
    sequence: int | None,
    *,
    status: HistoricalVehicleStatus = HistoricalVehicleStatus.STOPPED_AT,
    ordinal: int | None = None,
) -> VehicleObservation:
    observed = START + timedelta(seconds=seconds)
    source_naive = observed.replace(tzinfo=None)
    row_ordinal = seconds if ordinal is None else ordinal
    observation_id = vehicle_observation_id(
        trip_start_date=date(2024, 5, 15),
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
        observation_id=observation_id,
        source_lineage=(SourceLineageEntry("day.parquet", row_ordinal),),
        entity_id="entity-1",
        trip_id="trip-1",
        trip_start_date=date(2024, 5, 15),
        trip_start_time="08:00:00",
        schedule_relationship=TripScheduleRelationship.SCHEDULED,
        route_id="Red",
        direction_id=0,
        vehicle_id="vehicle-1",
        vehicle_label="train-1",
        observation_source_naive_utc=source_naive,
        observation_utc=observed,
        stop_sequence=sequence,
        stop_id=None if sequence is None else f"stop-{sequence}",
        current_status=status,
        latitude=None,
        longitude=None,
        bearing=None,
        speed=None,
        schema_version="test-v1",
    )


def test_episode_gap_boundary_is_strictly_greater_than_600_seconds() -> None:
    within = build_trip_episodes([_observation(0, 10), _observation(600, 20)])
    assert len(within.episodes) == 1
    assert within.episodes[0].maximum_gap_seconds == 600

    split = build_trip_episodes([_observation(0, 10), _observation(601, 20)])
    assert len(split.episodes) == 2
    assert split.gap_split_count == 1
    assert split.episodes[1].quality_flags == (EpisodeQualityFlag.EXCESSIVE_GAP,)


def test_stop_sequence_regression_splits_before_regression_and_isolates_recovery() -> None:
    observations = [
        _observation(0, 10),
        _observation(60, 20),
        _observation(120, 10),
        _observation(180, 15),
    ]
    result = build_trip_episodes(observations)
    by_id = {observation.observation_id: observation for observation in observations}
    sequences = [
        [by_id[observation_id].stop_sequence for observation_id in episode.observation_ids]
        for episode in result.episodes
    ]
    assert sequences == [[10, 20], [10, 15]]
    assert result.stop_sequence_regression_split_count == 1
    assert result.episodes[1].quality_flags == (EpisodeQualityFlag.STOP_SEQUENCE_REGRESSION,)


def test_same_timestamp_multiple_sequences_is_ambiguous_and_does_not_move_cursor() -> None:
    observations = [_observation(0, 10), _observation(0, 20), _observation(60, 5)]
    result = build_trip_episodes(reversed(observations))
    assert len(result.episodes) == 1
    assert result.ambiguous_event_group_count == 1
    assert result.stop_sequence_regression_split_count == 0
    assert result.episodes[0].quality_flags == (EpisodeQualityFlag.AMBIGUOUS_STOP_SEQUENCE,)


def test_multiple_statuses_at_one_sequence_remain_evidence_and_move_cursor() -> None:
    observations = [
        _observation(0, 10, status=HistoricalVehicleStatus.INCOMING_AT),
        _observation(0, 10, status=HistoricalVehicleStatus.STOPPED_AT),
        _observation(60, 5),
    ]
    result = build_trip_episodes(observations)
    assert len(result.episodes) == 2
    assert len(result.episodes[0].observation_ids) == 2
    assert result.stop_sequence_regression_split_count == 1


def test_raw_lineage_timestamp_regression_is_reported_but_canonical_order_wins() -> None:
    earlier = _observation(0, 10, ordinal=2)
    later = _observation(60, 20, ordinal=1)
    first = build_trip_episodes([later, earlier])
    second = build_trip_episodes([earlier, later])
    assert first == second
    assert first.raw_timestamp_regression_session_count == 1
    assert first.episodes[0].observation_ids == (
        earlier.observation_id,
        later.observation_id,
    )
    assert first.episodes[0].quality_flags == (EpisodeQualityFlag.RAW_TIMESTAMP_REGRESSION,)


def test_stopped_sequences_excludes_ambiguous_and_null_only_event_groups() -> None:
    observations = [
        _observation(0, 10),
        _observation(60, 20),
        _observation(60, 30),
        _observation(120, None),
        _observation(180, 40, status=HistoricalVehicleStatus.IN_TRANSIT_TO),
    ]
    result = build_trip_episodes(observations)
    by_id = {observation.observation_id: observation for observation in observations}
    assert stopped_sequences(result.episodes[0], by_id) == frozenset({10})


def test_gap_and_regression_boundary_preserves_both_quality_reasons() -> None:
    result = build_trip_episodes([_observation(0, 20), _observation(601, 10)])
    assert result.gap_split_count == 1
    assert result.stop_sequence_regression_split_count == 1
    assert result.episodes[1].quality_flags == (
        EpisodeQualityFlag.EXCESSIVE_GAP,
        EpisodeQualityFlag.STOP_SEQUENCE_REGRESSION,
    )
