from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any

import pytest
from arrive90_data_contracts.travel_time import (
    DownstreamOutcomeState,
    DownstreamStopExample,
    EpisodeQualityFlag,
    EpisodeScheduleMatchStatus,
    HistoricalVehicleStatus,
    SourceLineageEntry,
    TripEpisode,
    TripScheduleRelationship,
    VehicleObservation,
    canonical_float,
    decode_trip_schedule_relationship,
    decode_vehicle_status,
    downstream_example_id,
    trip_episode_id,
    vehicle_observation_id,
)

NOW = datetime(2024, 5, 15, 12, 0, tzinfo=UTC)


def _observation(**changes: object) -> VehicleObservation:
    values: dict[str, object] = {
        "source_lineage": (SourceLineageEntry("feeds/mbta_all/day.parquet", 12),),
        "entity_id": "entity-1",
        "trip_id": "trip-1",
        "trip_start_date": date(2024, 5, 15),
        "trip_start_time": "08:01:00",
        "schedule_relationship": TripScheduleRelationship.SCHEDULED,
        "route_id": "Red",
        "direction_id": 0,
        "vehicle_id": "vehicle-1",
        "vehicle_label": "1812",
        "observation_source_naive_utc": datetime(2024, 5, 15, 12, 0),
        "observation_utc": NOW,
        "stop_sequence": 40,
        "stop_id": "70068",
        "current_status": HistoricalVehicleStatus.STOPPED_AT,
        "latitude": 42.355,
        "longitude": -71.06,
        "bearing": 180.0,
        "speed": 0.0,
        "schema_version": "bus-observatory-parquet-v1",
    }
    values.update(changes)
    current_status = values["current_status"]
    if isinstance(current_status, HistoricalVehicleStatus):
        values.setdefault(
            "observation_id",
            vehicle_observation_id(
                trip_start_date=values["trip_start_date"],  # type: ignore[arg-type]
                trip_start_time=values["trip_start_time"],  # type: ignore[arg-type]
                trip_id=values["trip_id"],  # type: ignore[arg-type]
                route_id=values["route_id"],  # type: ignore[arg-type]
                direction_id=values["direction_id"],  # type: ignore[arg-type]
                vehicle_id=values["vehicle_id"],  # type: ignore[arg-type]
                observation_utc=values["observation_utc"],  # type: ignore[arg-type]
                stop_sequence=values["stop_sequence"],  # type: ignore[arg-type]
                current_status=current_status,
            ),
        )
    else:
        values.setdefault("observation_id", "invalid-until-contract-rejection")
    return VehicleObservation(**values)  # type: ignore[arg-type]


def _episode(**changes: object) -> TripEpisode:
    values: dict[str, object] = {
        "service_date": date(2024, 5, 15),
        "trip_id": "trip-1",
        "trip_start_time": "08:01:00",
        "route_id": "Red",
        "direction_id": 0,
        "vehicle_id": "vehicle-1",
        "first_observation_utc": NOW,
        "last_observation_utc": NOW,
        "observation_ids": ("a" * 64,),
        "maximum_gap_seconds": 0.0,
        "schedule_match_status": EpisodeScheduleMatchStatus.EXACT_MATCH,
        "schedule_version_id": "schedule-1",
        "route_pattern_id": "pattern-1",
        "quality_flags": (),
    }
    values.update(changes)
    values.setdefault(
        "episode_id",
        trip_episode_id(
            service_date=values["service_date"],  # type: ignore[arg-type]
            trip_id=values["trip_id"],  # type: ignore[arg-type]
            trip_start_time=values["trip_start_time"],  # type: ignore[arg-type]
            route_id=values["route_id"],  # type: ignore[arg-type]
            direction_id=values["direction_id"],  # type: ignore[arg-type]
            vehicle_id=values["vehicle_id"],  # type: ignore[arg-type]
            observation_ids=values["observation_ids"],  # type: ignore[arg-type]
        ),
    )
    return TripEpisode(**values)  # type: ignore[arg-type]


def _example(
    state: DownstreamOutcomeState = DownstreamOutcomeState.INTERVAL_RESOLVED,
    **changes: object,
) -> DownstreamStopExample:
    values: dict[str, object] = {
        "episode_id": "episode-1",
        "service_date": date(2024, 5, 15),
        "anchor_observation_id": "anchor-1",
        "feature_cutoff_utc": NOW,
        "origin_stop_id": "origin",
        "origin_stop_sequence": 10,
        "destination_stop_id": "destination",
        "destination_stop_sequence": 20,
        "destination_offset": 2,
        "scheduled_remaining_seconds": 600,
        "lower_evidence_observation_id": "lower-1",
        "upper_evidence_observation_id": "upper-1",
        "lower_bound_seconds": 100.0,
        "upper_bound_seconds": 200.0,
        "outcome_state": state,
        "base_weight": 0.5,
    }
    values.update(changes)
    values.setdefault(
        "example_id",
        downstream_example_id(
            episode_id=values["episode_id"],  # type: ignore[arg-type]
            anchor_observation_id=values["anchor_observation_id"],  # type: ignore[arg-type]
            destination_stop_id=values["destination_stop_id"],  # type: ignore[arg-type]
            destination_stop_sequence=values["destination_stop_sequence"],  # type: ignore[arg-type]
        ),
    )
    return DownstreamStopExample(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (0.0, TripScheduleRelationship.SCHEDULED),
        (1, TripScheduleRelationship.ADDED),
        ("unscheduled", TripScheduleRelationship.UNSCHEDULED),
        (3.0, TripScheduleRelationship.CANCELED),
        (5, TripScheduleRelationship.REPLACEMENT),
        (6, TripScheduleRelationship.DUPLICATED),
        (7, TripScheduleRelationship.DELETED),
    ],
)
def test_trip_relationship_decoder_accepts_only_known_values(
    raw: object, expected: TripScheduleRelationship
) -> None:
    assert decode_trip_schedule_relationship(raw) is expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (0.0, HistoricalVehicleStatus.INCOMING_AT),
        (1, HistoricalVehicleStatus.STOPPED_AT),
        ("in_transit_to", HistoricalVehicleStatus.IN_TRANSIT_TO),
    ],
)
def test_vehicle_status_decoder_accepts_only_known_values(
    raw: object, expected: HistoricalVehicleStatus
) -> None:
    assert decode_vehicle_status(raw) is expected


@pytest.mark.parametrize("raw", [4, 1.5, True, "UNKNOWN", None])
def test_enum_decoders_reject_unknown_integral_string_and_fractional_values(raw: object) -> None:
    with pytest.raises(ValueError, match="unknown"):
        decode_trip_schedule_relationship(raw)
    if raw not in (1.5,):
        with pytest.raises(ValueError, match="unknown"):
            decode_vehicle_status(raw)


def test_canonical_float_is_exact_and_normalizes_signed_zero() -> None:
    assert canonical_float(-0.0) == canonical_float(0.0) == "0x0.0p+0"
    assert canonical_float(1.5) == "0x1.8000000000000p+0"
    assert canonical_float(None) is None
    with pytest.raises(ValueError, match="finite"):
        canonical_float(math.nan)


def test_source_lineage_requires_nonempty_key_and_integer_ordinal() -> None:
    assert SourceLineageEntry("object.parquet", 0).source_row_ordinal == 0
    with pytest.raises(ValueError, match="source_object_key"):
        SourceLineageEntry("", 0)
    with pytest.raises(ValueError, match="nonnegative integer"):
        SourceLineageEntry("object.parquet", -1)
    with pytest.raises(ValueError, match="nonnegative integer"):
        SourceLineageEntry("object.parquet", 1.5)  # type: ignore[arg-type]


def test_vehicle_observation_binds_identity_lineage_and_exact_state_payload() -> None:
    observation = _observation()
    assert observation.observation_id == vehicle_observation_id(
        trip_start_date=observation.trip_start_date,
        trip_start_time=observation.trip_start_time,
        trip_id=observation.trip_id,
        route_id=observation.route_id,
        direction_id=observation.direction_id,
        vehicle_id=observation.vehicle_id,
        observation_utc=observation.observation_utc,
        stop_sequence=observation.stop_sequence,
        current_status=observation.current_status,
    )
    assert observation.canonical_state_payload[-1] == "0x0.0p+0"


def test_vehicle_observation_retains_sorted_complete_duplicate_lineage() -> None:
    first = SourceLineageEntry("a.parquet", 7)
    second = SourceLineageEntry("b.parquet", 1)
    observation = _observation(source_lineage=(first, second))
    assert observation.source_lineage == (first, second)
    with pytest.raises(ValueError, match="canonically sorted"):
        replace(observation, source_lineage=(second, first))
    with pytest.raises(ValueError, match="duplicate"):
        replace(observation, source_lineage=(first, first))


def test_vehicle_observation_rejects_identity_and_source_timestamp_drift() -> None:
    observation = _observation()
    with pytest.raises(ValueError, match="observation_id"):
        replace(observation, observation_id="wrong")
    with pytest.raises(ValueError, match="must not have timezone"):
        replace(observation, observation_source_naive_utc=NOW)
    with pytest.raises(ValueError, match="must be timezone-aware UTC"):
        replace(observation, observation_utc=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError, match="without clock arithmetic"):
        replace(
            observation,
            observation_source_naive_utc=datetime(2024, 5, 15, 8, 0),
        )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"direction_id": 2}, "direction_id"),
        ({"stop_sequence": -1}, "stop_sequence"),
        ({"schedule_relationship": "SCHEDULED"}, "schedule_relationship"),
        ({"current_status": "STOPPED_AT"}, "current_status"),
        ({"latitude": math.inf}, "latitude"),
        ({"latitude": 91.0}, "latitude"),
        ({"longitude": -181.0}, "longitude"),
        ({"bearing": 360.0}, "bearing"),
        ({"speed": -0.1}, "speed"),
        ({"trip_start_time": "8:00"}, "GTFS"),
    ],
)
def test_vehicle_observation_rejects_invalid_normalized_fields(
    changes: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        _observation(**changes)


def test_trip_episode_binds_ordered_observation_population() -> None:
    episode = _episode(
        observation_ids=("a" * 64, "b" * 64),
        last_observation_utc=NOW.replace(minute=5),
        maximum_gap_seconds=300,
        quality_flags=(EpisodeQualityFlag.AMBIGUOUS_STOP_SEQUENCE,),
    )
    assert episode.episode_id == trip_episode_id(
        service_date=episode.service_date,
        trip_id=episode.trip_id,
        trip_start_time=episode.trip_start_time,
        route_id=episode.route_id,
        direction_id=episode.direction_id,
        vehicle_id=episode.vehicle_id,
        observation_ids=episode.observation_ids,
    )
    with pytest.raises(ValueError, match="episode_id"):
        replace(episode, episode_id="wrong")


def test_trip_episode_enforces_gap_schedule_and_flag_contracts() -> None:
    episode = _episode()
    with pytest.raises(ValueError, match="no greater than 600"):
        replace(episode, maximum_gap_seconds=601)
    with pytest.raises(ValueError, match="inverted"):
        replace(episode, last_observation_utc=NOW.replace(hour=11))
    with pytest.raises(ValueError, match="nonempty and unique"):
        _episode(observation_ids=("a", "a"))
    with pytest.raises(ValueError, match="schedule match identifiers"):
        replace(
            episode,
            schedule_match_status=EpisodeScheduleMatchStatus.SCHEDULE_UNMATCHED,
        )
    with pytest.raises(ValueError, match="schedule_version_id"):
        replace(episode, schedule_version_id=None)
    with pytest.raises(ValueError, match="unique and bytewise sorted"):
        replace(
            episode,
            quality_flags=(
                EpisodeQualityFlag.STOP_SEQUENCE_REGRESSION,
                EpisodeQualityFlag.AMBIGUOUS_STOP_SEQUENCE,
            ),
        )


def test_finite_left_and_over_width_examples_freeze_interval_semantics() -> None:
    resolved = _example()
    assert resolved.included_in_likelihood

    left = _example(
        DownstreamOutcomeState.LEFT_CENSORED,
        lower_evidence_observation_id="anchor-1",
        lower_bound_seconds=0.0,
        upper_bound_seconds=120.0,
    )
    assert left.included_in_likelihood

    over_width = _example(
        DownstreamOutcomeState.OVER_WIDTH_INTERVAL,
        lower_bound_seconds=10.0,
        upper_bound_seconds=191.0,
    )
    assert not over_width.included_in_likelihood


def test_right_censored_example_uses_positive_infinity_without_event_evidence() -> None:
    example = _example(
        DownstreamOutcomeState.RIGHT_CENSORED,
        lower_evidence_observation_id="coverage-end",
        upper_evidence_observation_id=None,
        lower_bound_seconds=300.0,
        upper_bound_seconds=math.inf,
    )
    assert example.included_in_likelihood
    with pytest.raises(ValueError, match="positive infinity"):
        replace(example, upper_bound_seconds=3_600.0)
    with pytest.raises(ValueError, match="upper-event"):
        replace(example, upper_evidence_observation_id="future-event")


@pytest.mark.parametrize(
    "state",
    [
        DownstreamOutcomeState.MISSING_STOP_OBSERVATION,
        DownstreamOutcomeState.SESSION_DISCONTINUITY,
        DownstreamOutcomeState.NO_FOLLOW_UP,
    ],
)
def test_nonlikelihood_states_never_invent_finite_arrivals(state: DownstreamOutcomeState) -> None:
    example = _example(
        state,
        lower_evidence_observation_id=None,
        upper_evidence_observation_id=None,
        lower_bound_seconds=None,
        upper_bound_seconds=None,
    )
    assert not example.included_in_likelihood
    with pytest.raises(ValueError, match="cannot carry arrival"):
        replace(example, lower_bound_seconds=1.0)


def test_schedule_unmatched_example_has_no_synthetic_destination() -> None:
    example = _example(
        DownstreamOutcomeState.SCHEDULE_UNMATCHED,
        destination_stop_id=None,
        destination_stop_sequence=None,
        destination_offset=None,
        scheduled_remaining_seconds=None,
        lower_evidence_observation_id=None,
        upper_evidence_observation_id=None,
        lower_bound_seconds=None,
        upper_bound_seconds=None,
    )
    assert not example.included_in_likelihood
    with pytest.raises(ValueError, match="cannot carry a destination"):
        replace(example, destination_stop_id="invented")


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"example_id": "wrong"}, "example_id"),
        ({"destination_stop_sequence": 10}, "must follow"),
        ({"destination_offset": 9}, "between one and eight"),
        ({"scheduled_remaining_seconds": 1_801}, "1800"),
        ({"base_weight": 0.0}, "base_weight"),
        ({"lower_bound_seconds": 0.0}, "positive lower"),
        ({"upper_bound_seconds": 281.0}, "width limit"),
        ({"upper_evidence_observation_id": None}, "upper_evidence"),
    ],
)
def test_downstream_example_rejects_inconsistent_target_fields(
    changes: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        _example(**changes)


def test_left_censored_example_requires_anchor_lower_evidence_and_width_limit() -> None:
    with pytest.raises(ValueError, match="must be the anchor"):
        _example(
            DownstreamOutcomeState.LEFT_CENSORED,
            lower_bound_seconds=0.0,
            upper_bound_seconds=120.0,
        )
    with pytest.raises(ValueError, match="width limit"):
        _example(
            DownstreamOutcomeState.LEFT_CENSORED,
            lower_evidence_observation_id="anchor-1",
            lower_bound_seconds=0.0,
            upper_bound_seconds=181.0,
        )


def test_over_width_state_cannot_hide_a_qualifying_interval() -> None:
    with pytest.raises(ValueError, match="must exceed"):
        _example(
            DownstreamOutcomeState.OVER_WIDTH_INTERVAL,
            lower_bound_seconds=10.0,
            upper_bound_seconds=190.0,
        )
