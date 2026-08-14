"""Deterministic trip-episode construction from normalized vehicle observations."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise

from arrive90_data_contracts.travel_time import (
    EpisodeQualityFlag,
    EpisodeScheduleMatchStatus,
    HistoricalVehicleStatus,
    TripEpisode,
    VehicleObservation,
    trip_episode_id,
)

EPISODE_GAP_SECONDS = 600.0


@dataclass(frozen=True, slots=True)
class EpisodeBuildResult:
    """Canonical episodes plus ambiguity and raw-order quality counts."""

    episodes: tuple[TripEpisode, ...]
    ambiguous_event_group_count: int
    raw_timestamp_regression_session_count: int
    gap_split_count: int
    stop_sequence_regression_split_count: int


def _session_key(observation: VehicleObservation) -> tuple[object, ...]:
    return (
        observation.trip_start_date,
        observation.trip_start_time,
        observation.trip_id,
        observation.route_id,
        observation.direction_id,
        observation.vehicle_id,
    )


def _event_sort_key(observation: VehicleObservation) -> tuple[object, ...]:
    return (
        observation.observation_utc,
        observation.stop_sequence is None,
        observation.stop_sequence if observation.stop_sequence is not None else 0,
        observation.current_status.value.encode(),
        observation.observation_id,
    )


def _raw_timestamp_regression(observations: Iterable[VehicleObservation]) -> bool:
    by_object: dict[str, list[tuple[int, datetime]]] = defaultdict(list)
    for observation in observations:
        for lineage in observation.source_lineage:
            by_object[lineage.source_object_key].append(
                (lineage.source_row_ordinal, observation.observation_utc)
            )
    for values in by_object.values():
        ordered = sorted(values)
        if any(current[1] < previous[1] for previous, current in pairwise(ordered)):
            return True
    return False


def _episode(
    observations: list[VehicleObservation], quality_flags: set[EpisodeQualityFlag]
) -> TripEpisode:
    first = observations[0]
    ordered_ids = tuple(observation.observation_id for observation in observations)
    event_times = sorted({observation.observation_utc for observation in observations})
    gaps = [(current - previous).total_seconds() for previous, current in pairwise(event_times)]
    flags = tuple(sorted(quality_flags, key=lambda flag: flag.value.encode()))
    return TripEpisode(
        episode_id=trip_episode_id(
            service_date=first.trip_start_date,
            trip_id=first.trip_id,
            trip_start_time=first.trip_start_time,
            route_id=first.route_id,
            direction_id=first.direction_id,
            vehicle_id=first.vehicle_id,
            observation_ids=ordered_ids,
        ),
        service_date=first.trip_start_date,
        trip_id=first.trip_id,
        trip_start_time=first.trip_start_time,
        route_id=first.route_id,
        direction_id=first.direction_id,
        vehicle_id=first.vehicle_id,
        first_observation_utc=event_times[0],
        last_observation_utc=event_times[-1],
        observation_ids=ordered_ids,
        maximum_gap_seconds=max(gaps, default=0.0),
        schedule_match_status=EpisodeScheduleMatchStatus.SCHEDULE_UNMATCHED,
        schedule_version_id=None,
        route_pattern_id=None,
        quality_flags=flags,
    )


def build_trip_episodes(observations: Iterable[VehicleObservation]) -> EpisodeBuildResult:
    """Build episodes under the frozen session, gap, event, and regression rules."""

    sessions: dict[tuple[object, ...], list[VehicleObservation]] = defaultdict(list)
    for observation in observations:
        sessions[_session_key(observation)].append(observation)

    episodes: list[TripEpisode] = []
    ambiguous_groups = 0
    raw_regression_sessions = 0
    gap_splits = 0
    sequence_splits = 0
    for session_key in sorted(sessions, key=repr):
        session = sorted(sessions[session_key], key=_event_sort_key)
        has_raw_regression = _raw_timestamp_regression(session)
        if has_raw_regression:
            raw_regression_sessions += 1
        by_time: dict[datetime, list[VehicleObservation]] = defaultdict(list)
        for observation in session:
            by_time[observation.observation_utc].append(observation)

        current: list[VehicleObservation] = []
        quality_flags: set[EpisodeQualityFlag] = set()
        if has_raw_regression:
            quality_flags.add(EpisodeQualityFlag.RAW_TIMESTAMP_REGRESSION)
        previous_time: datetime | None = None
        sequence_cursor: int | None = None
        for event_time in sorted(by_time):
            event = sorted(by_time[event_time], key=_event_sort_key)
            sequences = {
                observation.stop_sequence
                for observation in event
                if observation.stop_sequence is not None
            }
            ambiguous = len(sequences) > 1
            event_sequence = next(iter(sequences)) if len(sequences) == 1 else None
            gap_boundary = (
                previous_time is not None
                and (event_time - previous_time).total_seconds() > EPISODE_GAP_SECONDS
            )
            regression_boundary = (
                event_sequence is not None
                and sequence_cursor is not None
                and event_sequence < sequence_cursor
            )
            if current and (gap_boundary or regression_boundary):
                episodes.append(_episode(current, quality_flags))
                current = []
                quality_flags = set()
                if has_raw_regression:
                    quality_flags.add(EpisodeQualityFlag.RAW_TIMESTAMP_REGRESSION)
                sequence_cursor = None
                if gap_boundary:
                    gap_splits += 1
                    quality_flags.add(EpisodeQualityFlag.EXCESSIVE_GAP)
                if regression_boundary:
                    sequence_splits += 1
                    quality_flags.add(EpisodeQualityFlag.STOP_SEQUENCE_REGRESSION)
            current.extend(event)
            if ambiguous:
                ambiguous_groups += 1
                quality_flags.add(EpisodeQualityFlag.AMBIGUOUS_STOP_SEQUENCE)
            elif event_sequence is not None:
                sequence_cursor = (
                    event_sequence
                    if sequence_cursor is None
                    else max(sequence_cursor, event_sequence)
                )
            previous_time = event_time
        if current:
            episodes.append(_episode(current, quality_flags))

    episodes.sort(
        key=lambda episode: (
            episode.service_date,
            episode.trip_start_time.encode(),
            episode.trip_id.encode(),
            episode.route_id.encode(),
            episode.direction_id,
            episode.vehicle_id.encode(),
            episode.first_observation_utc,
            episode.episode_id,
        )
    )
    return EpisodeBuildResult(
        episodes=tuple(episodes),
        ambiguous_event_group_count=ambiguous_groups,
        raw_timestamp_regression_session_count=raw_regression_sessions,
        gap_split_count=gap_splits,
        stop_sequence_regression_split_count=sequence_splits,
    )


def stopped_sequences(
    episode: TripEpisode,
    observations_by_id: dict[str, VehicleObservation],
) -> frozenset[int]:
    """Return unambiguous stopped sequences eligible for the one-day support gate."""

    by_time: dict[datetime, list[VehicleObservation]] = defaultdict(list)
    for observation_id in episode.observation_ids:
        observation = observations_by_id[observation_id]
        by_time[observation.observation_utc].append(observation)
    retained: set[int] = set()
    for event in by_time.values():
        sequences = {
            observation.stop_sequence
            for observation in event
            if observation.stop_sequence is not None
        }
        if len(sequences) != 1:
            continue
        sequence = next(iter(sequences))
        if any(
            observation.stop_sequence == sequence
            and observation.current_status is HistoricalVehicleStatus.STOPPED_AT
            for observation in event
        ):
            retained.add(sequence)
    return frozenset(retained)
