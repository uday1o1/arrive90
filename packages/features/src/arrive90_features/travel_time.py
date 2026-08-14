"""Point-in-observation travel-time feature view and deterministic row builder."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise
from zoneinfo import ZoneInfo

from arrive90_data_contracts.realtime import require_utc
from arrive90_data_contracts.travel_time import (
    HistoricalVehicleStatus,
    TripEpisode,
    VehicleObservation,
)
from arrive90_ingestion.historical_schedule import (
    EpisodeScheduleMatch,
    ScheduledStop,
    ScheduleMatchReason,
)

type TravelTimeFeatureValue = str | int | float | bool | None

BOSTON = ZoneInfo("America/New_York")
FEATURE_SCHEMA_VERSION = "travel-time-observation-v1"


class FutureObservationAccessError(ValueError):
    """Raised when feature code attempts to read beyond the anchor cutoff."""


@dataclass(frozen=True, slots=True)
class ObservationCutoffView:
    """An episode view that exposes only observations at or before one cutoff."""

    episode: TripEpisode
    cutoff_utc: datetime
    observations: tuple[VehicleObservation, ...]

    def __post_init__(self) -> None:
        require_utc(self.cutoff_utc, "cutoff_utc")
        expected_ids = set(self.episode.observation_ids)
        actual_ids = {observation.observation_id for observation in self.observations}
        if expected_ids != actual_ids:
            raise ValueError("cutoff view must receive every and only episode observation")
        if len(actual_ids) != len(self.observations):
            raise ValueError("cutoff view observations must be unique")

    @classmethod
    def from_episode(
        cls,
        episode: TripEpisode,
        observations_by_id: dict[str, VehicleObservation],
        *,
        cutoff_utc: datetime,
    ) -> ObservationCutoffView:
        """Construct a complete episode-backed view with a frozen public cutoff."""

        observations = tuple(
            sorted(
                (observations_by_id[identifier] for identifier in episode.observation_ids),
                key=lambda observation: (
                    observation.observation_utc,
                    observation.observation_id,
                ),
            )
        )
        return cls(episode=episode, cutoff_utc=cutoff_utc, observations=observations)

    def available(self) -> tuple[VehicleObservation, ...]:
        """Return canonical observations available at the cutoff."""

        return tuple(
            observation
            for observation in self.observations
            if observation.observation_utc <= self.cutoff_utc
        )

    def observation(self, observation_id: str) -> VehicleObservation:
        """Return one available observation and reject deliberate future access."""

        for observation in self.observations:
            if observation.observation_id != observation_id:
                continue
            if observation.observation_utc > self.cutoff_utc:
                raise FutureObservationAccessError("observation occurs after the feature cutoff")
            return observation
        raise KeyError(observation_id)


@dataclass(frozen=True, slots=True)
class TravelTimeFeatureRow:
    """One deterministic raw feature row with cutoff and source lineage."""

    anchor_observation_id: str
    feature_cutoff_utc: datetime
    schedule_version_id: str
    route_pattern_id: str
    feature_schema_version: str
    values: tuple[tuple[str, TravelTimeFeatureValue], ...]
    source_observation_ids: tuple[str, ...]
    source_lineage_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        require_utc(self.feature_cutoff_utc, "feature_cutoff_utc")
        if self.values != tuple(sorted(self.values, key=lambda item: item[0].encode())):
            raise ValueError("feature values must be unique and bytewise sorted")
        if len({name for name, _ in self.values}) != len(self.values):
            raise ValueError("feature values cannot contain duplicate names")


def _stop_index(stops: tuple[ScheduledStop, ...], stop_id: str, sequence: int) -> int:
    matches = [
        index
        for index, stop in enumerate(stops)
        if (stop.stop_id, stop.stop_sequence) == (stop_id, sequence)
    ]
    if len(matches) != 1:
        raise ValueError("feature stop identity must match exactly one scheduled stop")
    return matches[0]


def _stopped_prefix(
    observations: tuple[VehicleObservation, ...],
) -> tuple[VehicleObservation, ...]:
    by_time: dict[datetime, list[VehicleObservation]] = {}
    for observation in observations:
        by_time.setdefault(observation.observation_utc, []).append(observation)
    first_by_sequence: dict[int, VehicleObservation] = {}
    for observed_at in sorted(by_time):
        event = by_time[observed_at]
        sequences = {
            observation.stop_sequence
            for observation in event
            if observation.stop_sequence is not None
        }
        if len(sequences) != 1:
            continue
        sequence = next(iter(sequences))
        candidates = [
            observation
            for observation in event
            if observation.stop_sequence == sequence
            and observation.current_status is HistoricalVehicleStatus.STOPPED_AT
        ]
        if candidates and sequence not in first_by_sequence:
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


def build_travel_time_feature_row(
    match: EpisodeScheduleMatch,
    view: ObservationCutoffView,
    *,
    anchor_observation_id: str,
    destination_stop_id: str,
    destination_stop_sequence: int,
    destination_offset: int,
    scheduled_remaining_seconds: int,
) -> TravelTimeFeatureRow:
    """Build one raw feature row using only the observation-cutoff view."""

    if match.reason is not ScheduleMatchReason.EXACT or match.scheduled_trip is None:
        raise ValueError("travel-time features require an exact schedule match")
    if match.episode.episode_id != view.episode.episode_id:
        raise ValueError("feature view episode does not match the schedule match")
    anchor = view.observation(anchor_observation_id)
    if view.cutoff_utc != anchor.observation_utc:
        raise ValueError("feature cutoff must equal the anchor observation timestamp")
    trip = match.scheduled_trip
    if trip.published_at_utc > view.cutoff_utc:
        raise FutureObservationAccessError("schedule version was published after the cutoff")
    if anchor.stop_id is None or anchor.stop_sequence is None:
        raise ValueError("feature anchor requires complete stop identity")
    origin_index = _stop_index(trip.stops, anchor.stop_id, anchor.stop_sequence)
    destination_index = _stop_index(trip.stops, destination_stop_id, destination_stop_sequence)
    if destination_index - origin_index != destination_offset:
        raise ValueError("destination offset does not match the scheduled trip")
    origin_stop = trip.stops[origin_index]
    destination_stop = trip.stops[destination_index]
    computed_remaining = int(
        (destination_stop.arrival_utc - origin_stop.arrival_utc).total_seconds()
    )
    if computed_remaining != scheduled_remaining_seconds:
        raise ValueError("scheduled remaining duration does not match the scheduled trip")

    available = view.available()
    if not available:
        raise ValueError("feature view has no available observations")
    stopped = _stopped_prefix(available)
    stopped_before = tuple(
        observation
        for observation in stopped
        if observation.observation_utc < anchor.observation_utc
    )
    completed_durations = [
        (current.observation_utc - previous.observation_utc).total_seconds()
        for previous, current in pairwise(stopped)
        if current.observation_utc <= anchor.observation_utc
    ]
    event_times = sorted({observation.observation_utc for observation in available})
    previous_event_times = [
        event_time for event_time in event_times if event_time < view.cutoff_utc
    ]
    previous_gap = (
        (view.cutoff_utc - previous_event_times[-1]).total_seconds()
        if previous_event_times
        else None
    )
    local = view.cutoff_utc.astimezone(BOSTON)
    local_seconds = local.hour * 3600 + local.minute * 60 + local.second
    progress_denominator = max(len(trip.stops) - 1, 1)
    values: dict[str, TravelTimeFeatureValue] = {
        "anchor_bearing": anchor.bearing,
        "anchor_bearing_missing": anchor.bearing is None,
        "anchor_latitude": anchor.latitude,
        "anchor_latitude_missing": anchor.latitude is None,
        "anchor_longitude": anchor.longitude,
        "anchor_longitude_missing": anchor.longitude is None,
        "anchor_speed": anchor.speed,
        "anchor_speed_missing": anchor.speed is None,
        "day_of_week_cos": math.cos(2 * math.pi * local.weekday() / 7),
        "day_of_week_sin": math.sin(2 * math.pi * local.weekday() / 7),
        "destination_stop_id": destination_stop_id,
        "destination_stop_sequence": destination_stop_sequence,
        "direction_id": match.episode.direction_id,
        "elapsed_episode_seconds": (
            view.cutoff_utc - min(item.observation_utc for item in available)
        ).total_seconds(),
        "local_time_cos": math.cos(2 * math.pi * local_seconds / 86_400),
        "local_time_sin": math.sin(2 * math.pi * local_seconds / 86_400),
        "median_last_three_segment_seconds": (
            statistics.median(completed_durations[-3:]) if completed_durations else None
        ),
        "most_recent_observation_gap_seconds": previous_gap,
        "observed_origin_lateness_seconds": (
            anchor.observation_utc - origin_stop.arrival_utc
        ).total_seconds(),
        "observed_stops_before_anchor": len(stopped_before),
        "origin_stop_id": anchor.stop_id,
        "origin_stop_sequence": anchor.stop_sequence,
        "previous_stopped_segment_seconds": (
            (anchor.observation_utc - stopped_before[-1].observation_utc).total_seconds()
            if stopped_before
            else None
        ),
        "remaining_scheduled_stop_count": destination_offset,
        "route_id": match.episode.route_id,
        "route_pattern_id": trip.route_pattern_id,
        "scheduled_progress_fraction": origin_index / progress_denominator,
        "scheduled_remaining_seconds": scheduled_remaining_seconds,
        "trip_start_hour": int(match.episode.trip_start_time.split(":", maxsplit=1)[0]),
        "weekend": local.weekday() >= 5,
    }
    lineage = {
        f"{entry.source_object_key}:{entry.source_row_ordinal}"
        for observation in available
        for entry in observation.source_lineage
    }
    return TravelTimeFeatureRow(
        anchor_observation_id=anchor_observation_id,
        feature_cutoff_utc=view.cutoff_utc,
        schedule_version_id=trip.schedule_version_id,
        route_pattern_id=trip.route_pattern_id,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        values=tuple(sorted(values.items(), key=lambda item: item[0].encode())),
        source_observation_ids=tuple(
            sorted(
                (observation.observation_id for observation in available),
                key=str.encode,
            )
        ),
        source_lineage_keys=tuple(sorted(lineage, key=str.encode)),
    )
