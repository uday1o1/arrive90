"""Leakage-resistant downstream stop destination and interval target construction."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise

from arrive90_data_contracts.travel_time import (
    MAX_DESTINATION_OFFSET,
    MAX_FINITE_INTERVAL_WIDTH_SECONDS,
    MAX_SCHEDULED_REMAINING_SECONDS,
    DownstreamOutcomeState,
    DownstreamStopExample,
    EpisodeQualityFlag,
    HistoricalVehicleStatus,
    VehicleObservation,
    downstream_example_id,
)
from arrive90_ingestion.historical_schedule import (
    EpisodeScheduleMatch,
    ScheduledStop,
    ScheduleMatchReason,
)


@dataclass(frozen=True, slots=True)
class TargetBuildResult:
    """Canonical examples and complete population accounting."""

    examples: tuple[DownstreamStopExample, ...]
    anchor_count: int
    matched_anchor_count: int
    terminal_anchor_count: int
    outcome_state_counts: tuple[tuple[str, int], ...]


def _event_groups(
    match: EpisodeScheduleMatch,
    observations_by_id: dict[str, VehicleObservation],
) -> tuple[tuple[datetime, tuple[VehicleObservation, ...]], ...]:
    by_time: dict[datetime, list[VehicleObservation]] = defaultdict(list)
    for observation_id in match.episode.observation_ids:
        observation = observations_by_id[observation_id]
        by_time[observation.observation_utc].append(observation)
    return tuple(
        (
            observed_at,
            tuple(
                sorted(
                    observations,
                    key=lambda observation: (
                        observation.stop_sequence is None,
                        observation.stop_sequence if observation.stop_sequence is not None else 0,
                        observation.current_status.value.encode(),
                        observation.observation_id,
                    ),
                )
            ),
        )
        for observed_at, observations in sorted(by_time.items())
    )


def _unambiguous_sequence(event: tuple[VehicleObservation, ...]) -> int | None:
    sequences = {
        observation.stop_sequence for observation in event if observation.stop_sequence is not None
    }
    return next(iter(sequences)) if len(sequences) == 1 else None


def _anchors(
    groups: tuple[tuple[datetime, tuple[VehicleObservation, ...]], ...],
) -> tuple[VehicleObservation, ...]:
    first_by_sequence: dict[int, VehicleObservation] = {}
    for _, event in groups:
        sequence = _unambiguous_sequence(event)
        if sequence is None or sequence in first_by_sequence:
            continue
        candidates = [
            observation
            for observation in event
            if observation.stop_sequence == sequence
            and observation.stop_id is not None
            and observation.current_status is HistoricalVehicleStatus.STOPPED_AT
        ]
        if candidates:
            first_by_sequence[sequence] = min(
                candidates, key=lambda observation: observation.observation_id
            )
    return tuple(
        sorted(
            first_by_sequence.values(),
            key=lambda observation: (
                observation.observation_utc,
                observation.stop_sequence if observation.stop_sequence is not None else -1,
                observation.observation_id,
            ),
        )
    )


def _destinations(
    anchor: VehicleObservation, stops: tuple[ScheduledStop, ...]
) -> tuple[tuple[int, ScheduledStop, int], ...]:
    origin_index = next(
        (
            index
            for index, stop in enumerate(stops)
            if (stop.stop_sequence, stop.stop_id) == (anchor.stop_sequence, anchor.stop_id)
        ),
        None,
    )
    if origin_index is None:
        return ()
    origin = stops[origin_index]
    selected: list[tuple[int, ScheduledStop, int]] = []
    for offset, destination in enumerate(
        stops[origin_index + 1 : origin_index + 1 + MAX_DESTINATION_OFFSET], start=1
    ):
        remaining = int((destination.arrival_utc - origin.arrival_utc).total_seconds())
        if 0 < remaining <= MAX_SCHEDULED_REMAINING_SECONDS:
            selected.append((offset, destination, remaining))
    return tuple(selected)


def _upper_evidence(
    groups: tuple[tuple[datetime, tuple[VehicleObservation, ...]], ...],
    *,
    anchor: VehicleObservation,
    destination: ScheduledStop,
) -> VehicleObservation | None:
    for observed_at, event in groups:
        if observed_at <= anchor.observation_utc:
            continue
        if _unambiguous_sequence(event) != destination.stop_sequence:
            continue
        candidates = [
            observation
            for observation in event
            if observation.stop_sequence == destination.stop_sequence
            and observation.stop_id == destination.stop_id
            and observation.current_status is HistoricalVehicleStatus.STOPPED_AT
        ]
        if candidates:
            return min(candidates, key=lambda observation: observation.observation_id)
    return None


def _lower_evidence(
    groups: tuple[tuple[datetime, tuple[VehicleObservation, ...]], ...],
    *,
    anchor: VehicleObservation,
    destination: ScheduledStop,
    before_utc: datetime,
) -> VehicleObservation:
    candidates: list[VehicleObservation] = [anchor]
    for observed_at, event in groups:
        if observed_at < anchor.observation_utc or observed_at >= before_utc:
            continue
        sequence = _unambiguous_sequence(event)
        if sequence is None:
            continue
        if sequence < destination.stop_sequence:
            candidates.extend(
                observation for observation in event if observation.stop_sequence == sequence
            )
        elif sequence == destination.stop_sequence:
            candidates.extend(
                observation
                for observation in event
                if observation.stop_sequence == sequence
                and observation.current_status is not HistoricalVehicleStatus.STOPPED_AT
            )
    return max(
        candidates,
        key=lambda observation: (observation.observation_utc, observation.observation_id),
    )


def _observed_beyond_destination(
    groups: tuple[tuple[datetime, tuple[VehicleObservation, ...]], ...],
    *,
    anchor: VehicleObservation,
    destination: ScheduledStop,
) -> bool:
    return any(
        observed_at > anchor.observation_utc
        and (sequence := _unambiguous_sequence(event)) is not None
        and sequence > destination.stop_sequence
        for observed_at, event in groups
    )


def _latest_follow_up(
    groups: tuple[tuple[datetime, tuple[VehicleObservation, ...]], ...],
    *,
    anchor: VehicleObservation,
) -> VehicleObservation | None:
    candidates = [
        observation
        for observed_at, event in groups
        if observed_at > anchor.observation_utc
        for observation in event
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda observation: (observation.observation_utc, observation.observation_id),
    )


def _make_example(
    match: EpisodeScheduleMatch,
    *,
    anchor: VehicleObservation,
    destination: ScheduledStop,
    destination_offset: int,
    scheduled_remaining_seconds: int,
    base_weight: float,
    lower_evidence_observation_id: str | None,
    upper_evidence_observation_id: str | None,
    lower_bound_seconds: float | None,
    upper_bound_seconds: float | None,
    outcome_state: DownstreamOutcomeState,
) -> DownstreamStopExample:
    if anchor.stop_id is None or anchor.stop_sequence is None:
        raise ValueError("prediction anchors require complete stop identity")
    return DownstreamStopExample(
        example_id=downstream_example_id(
            episode_id=match.episode.episode_id,
            anchor_observation_id=anchor.observation_id,
            destination_stop_id=destination.stop_id,
            destination_stop_sequence=destination.stop_sequence,
        ),
        episode_id=match.episode.episode_id,
        service_date=match.episode.service_date,
        anchor_observation_id=anchor.observation_id,
        feature_cutoff_utc=anchor.observation_utc,
        origin_stop_id=anchor.stop_id,
        origin_stop_sequence=anchor.stop_sequence,
        destination_stop_id=destination.stop_id,
        destination_stop_sequence=destination.stop_sequence,
        destination_offset=destination_offset,
        scheduled_remaining_seconds=scheduled_remaining_seconds,
        lower_evidence_observation_id=lower_evidence_observation_id,
        upper_evidence_observation_id=upper_evidence_observation_id,
        lower_bound_seconds=lower_bound_seconds,
        upper_bound_seconds=upper_bound_seconds,
        outcome_state=outcome_state,
        base_weight=base_weight,
    )


def _example(
    match: EpisodeScheduleMatch,
    *,
    anchor: VehicleObservation,
    destination: ScheduledStop,
    destination_offset: int,
    scheduled_remaining_seconds: int,
    base_weight: float,
    groups: tuple[tuple[datetime, tuple[VehicleObservation, ...]], ...],
    session_discontinues: bool,
) -> DownstreamStopExample:
    upper = _upper_evidence(groups, anchor=anchor, destination=destination)
    if upper is not None:
        lower = _lower_evidence(
            groups,
            anchor=anchor,
            destination=destination,
            before_utc=upper.observation_utc,
        )
        lower_seconds = (lower.observation_utc - anchor.observation_utc).total_seconds()
        upper_seconds = (upper.observation_utc - anchor.observation_utc).total_seconds()
        width = upper_seconds - lower_seconds
        if width > MAX_FINITE_INTERVAL_WIDTH_SECONDS:
            state = DownstreamOutcomeState.OVER_WIDTH_INTERVAL
        elif lower_seconds == 0:
            state = DownstreamOutcomeState.LEFT_CENSORED
        else:
            state = DownstreamOutcomeState.INTERVAL_RESOLVED
        return _make_example(
            match,
            anchor=anchor,
            destination=destination,
            destination_offset=destination_offset,
            scheduled_remaining_seconds=scheduled_remaining_seconds,
            base_weight=base_weight,
            lower_evidence_observation_id=lower.observation_id,
            upper_evidence_observation_id=upper.observation_id,
            lower_bound_seconds=lower_seconds,
            upper_bound_seconds=upper_seconds,
            outcome_state=state,
        )
    if _observed_beyond_destination(groups, anchor=anchor, destination=destination):
        state = DownstreamOutcomeState.MISSING_STOP_OBSERVATION
        follow_up = None
    elif session_discontinues:
        state = DownstreamOutcomeState.SESSION_DISCONTINUITY
        follow_up = None
    else:
        follow_up = _latest_follow_up(groups, anchor=anchor)
        state = (
            DownstreamOutcomeState.RIGHT_CENSORED
            if follow_up is not None
            else DownstreamOutcomeState.NO_FOLLOW_UP
        )
    if state is DownstreamOutcomeState.RIGHT_CENSORED and follow_up is not None:
        duration = min(
            (follow_up.observation_utc - anchor.observation_utc).total_seconds(), 3_600.0
        )
        return _make_example(
            match,
            anchor=anchor,
            destination=destination,
            destination_offset=destination_offset,
            scheduled_remaining_seconds=scheduled_remaining_seconds,
            base_weight=base_weight,
            lower_evidence_observation_id=follow_up.observation_id,
            upper_evidence_observation_id=None,
            lower_bound_seconds=duration,
            upper_bound_seconds=math.inf,
            outcome_state=state,
        )
    return _make_example(
        match,
        anchor=anchor,
        destination=destination,
        destination_offset=destination_offset,
        scheduled_remaining_seconds=scheduled_remaining_seconds,
        base_weight=base_weight,
        lower_evidence_observation_id=None,
        upper_evidence_observation_id=None,
        lower_bound_seconds=None,
        upper_bound_seconds=None,
        outcome_state=state,
    )


def _unmatched_example(
    match: EpisodeScheduleMatch, anchor: VehicleObservation
) -> DownstreamStopExample:
    if anchor.stop_id is None or anchor.stop_sequence is None:
        raise ValueError("prediction anchors require complete stop identity")
    return DownstreamStopExample(
        example_id=downstream_example_id(
            episode_id=match.episode.episode_id,
            anchor_observation_id=anchor.observation_id,
            destination_stop_id=None,
            destination_stop_sequence=None,
        ),
        episode_id=match.episode.episode_id,
        service_date=match.episode.service_date,
        anchor_observation_id=anchor.observation_id,
        feature_cutoff_utc=anchor.observation_utc,
        origin_stop_id=anchor.stop_id,
        origin_stop_sequence=anchor.stop_sequence,
        destination_stop_id=None,
        destination_stop_sequence=None,
        destination_offset=None,
        scheduled_remaining_seconds=None,
        lower_evidence_observation_id=None,
        upper_evidence_observation_id=None,
        lower_bound_seconds=None,
        upper_bound_seconds=None,
        outcome_state=DownstreamOutcomeState.SCHEDULE_UNMATCHED,
        base_weight=1.0,
    )


def _discontinuous_episode_ids(
    matches: tuple[EpisodeScheduleMatch, ...],
) -> frozenset[str]:
    sessions: dict[tuple[object, ...], list[EpisodeScheduleMatch]] = defaultdict(list)
    for match in matches:
        episode = match.episode
        sessions[
            (
                episode.service_date,
                episode.trip_id,
                episode.trip_start_time,
                episode.route_id,
                episode.direction_id,
                episode.vehicle_id,
            )
        ].append(match)
    discontinuous: set[str] = set()
    boundary_flags = {
        EpisodeQualityFlag.EXCESSIVE_GAP,
        EpisodeQualityFlag.STOP_SEQUENCE_REGRESSION,
    }
    for session in sessions.values():
        ordered = sorted(
            session,
            key=lambda match: (
                match.episode.first_observation_utc,
                match.episode.episode_id,
            ),
        )
        for previous, current in pairwise(ordered):
            if boundary_flags.intersection(current.episode.quality_flags):
                discontinuous.add(previous.episode.episode_id)
    return frozenset(discontinuous)


def build_downstream_examples(
    matches: tuple[EpisodeScheduleMatch, ...],
    observations_by_id: dict[str, VehicleObservation],
) -> TargetBuildResult:
    """Generate bounded destinations and interval outcomes without feature-time access."""

    discontinuous = _discontinuous_episode_ids(matches)
    examples: list[DownstreamStopExample] = []
    anchor_count = 0
    matched_anchor_count = 0
    terminal_anchor_count = 0
    for match in matches:
        groups = _event_groups(match, observations_by_id)
        anchors = _anchors(groups)
        anchor_count += len(anchors)
        if match.reason is not ScheduleMatchReason.EXACT or match.scheduled_trip is None:
            examples.extend(_unmatched_example(match, anchor) for anchor in anchors)
            continue
        matched_anchor_count += len(anchors)
        for anchor in anchors:
            destinations = _destinations(anchor, match.scheduled_trip.stops)
            if not destinations:
                terminal_anchor_count += 1
                continue
            base_weight = 1.0 / len(destinations)
            examples.extend(
                _example(
                    match,
                    anchor=anchor,
                    destination=destination,
                    destination_offset=offset,
                    scheduled_remaining_seconds=remaining,
                    base_weight=base_weight,
                    groups=groups,
                    session_discontinues=match.episode.episode_id in discontinuous,
                )
                for offset, destination, remaining in destinations
            )
    examples.sort(
        key=lambda example: (
            example.service_date,
            example.feature_cutoff_utc,
            example.episode_id,
            example.anchor_observation_id,
            example.destination_offset if example.destination_offset is not None else 0,
            example.example_id,
        )
    )
    state_counts = Counter(example.outcome_state.value for example in examples)
    return TargetBuildResult(
        examples=tuple(examples),
        anchor_count=anchor_count,
        matched_anchor_count=matched_anchor_count,
        terminal_anchor_count=terminal_anchor_count,
        outcome_state_counts=tuple(sorted(state_counts.items(), key=lambda item: item[0].encode())),
    )
