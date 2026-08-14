"""Frozen normalized contracts for the travel-time-v1 experiment."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

from arrive90_data_contracts.realtime import require_utc

PRIMARY_EPISODE_GAP_SECONDS = 600.0
MAX_DESTINATION_OFFSET = 8
MAX_SCHEDULED_REMAINING_SECONDS = 1_800
MAX_FINITE_INTERVAL_WIDTH_SECONDS = 180.0


class TripScheduleRelationship(StrEnum):
    """Known GTFS-Realtime TripDescriptor schedule relationships."""

    SCHEDULED = "SCHEDULED"
    ADDED = "ADDED"
    UNSCHEDULED = "UNSCHEDULED"
    CANCELED = "CANCELED"
    REPLACEMENT = "REPLACEMENT"
    DUPLICATED = "DUPLICATED"
    DELETED = "DELETED"


class HistoricalVehicleStatus(StrEnum):
    """Known GTFS-Realtime VehiclePosition stop statuses."""

    INCOMING_AT = "INCOMING_AT"
    STOPPED_AT = "STOPPED_AT"
    IN_TRANSIT_TO = "IN_TRANSIT_TO"


class EpisodeScheduleMatchStatus(StrEnum):
    """Deterministic schedule matching result for one trip episode."""

    EXACT_MATCH = "EXACT_MATCH"
    SCHEDULE_UNMATCHED = "SCHEDULE_UNMATCHED"
    SCHEDULE_VERSION_CONFLICT = "SCHEDULE_VERSION_CONFLICT"


class EpisodeQualityFlag(StrEnum):
    """Episode-level quality conditions preserved for reporting."""

    AMBIGUOUS_STOP_SEQUENCE = "AMBIGUOUS_STOP_SEQUENCE"
    EXCESSIVE_GAP = "EXCESSIVE_GAP"
    RAW_TIMESTAMP_REGRESSION = "RAW_TIMESTAMP_REGRESSION"
    STOP_SEQUENCE_REGRESSION = "STOP_SEQUENCE_REGRESSION"


class DownstreamOutcomeState(StrEnum):
    """Finite, censored, and excluded downstream observation states."""

    INTERVAL_RESOLVED = "INTERVAL_RESOLVED"
    LEFT_CENSORED = "LEFT_CENSORED"
    RIGHT_CENSORED = "RIGHT_CENSORED"
    OVER_WIDTH_INTERVAL = "OVER_WIDTH_INTERVAL"
    MISSING_STOP_OBSERVATION = "MISSING_STOP_OBSERVATION"
    SESSION_DISCONTINUITY = "SESSION_DISCONTINUITY"
    SCHEDULE_UNMATCHED = "SCHEDULE_UNMATCHED"
    NO_FOLLOW_UP = "NO_FOLLOW_UP"


_TRIP_RELATIONSHIP_BY_SOURCE_VALUE = {
    0: TripScheduleRelationship.SCHEDULED,
    1: TripScheduleRelationship.ADDED,
    2: TripScheduleRelationship.UNSCHEDULED,
    3: TripScheduleRelationship.CANCELED,
    5: TripScheduleRelationship.REPLACEMENT,
    6: TripScheduleRelationship.DUPLICATED,
    7: TripScheduleRelationship.DELETED,
}
_VEHICLE_STATUS_BY_SOURCE_VALUE = {
    0: HistoricalVehicleStatus.INCOMING_AT,
    1: HistoricalVehicleStatus.STOPPED_AT,
    2: HistoricalVehicleStatus.IN_TRANSIT_TO,
}


def _decode_enum[T: StrEnum](
    value: object,
    *,
    field: str,
    enum_type: type[T],
    numeric_values: dict[int, T],
) -> T:
    if isinstance(value, enum_type):
        return value
    if isinstance(value, str):
        try:
            return enum_type(value.strip().upper())
        except ValueError as error:
            raise ValueError(f"unknown {field}: {value!r}") from error
    if isinstance(value, int | float) and not isinstance(value, bool):
        numeric = float(value)
        if numeric.is_integer() and int(numeric) in numeric_values:
            return numeric_values[int(numeric)]
    raise ValueError(f"unknown {field}: {value!r}")


def decode_trip_schedule_relationship(value: object) -> TripScheduleRelationship:
    """Decode one documented numeric or canonical string relationship."""

    return _decode_enum(
        value,
        field="trip schedule relationship",
        enum_type=TripScheduleRelationship,
        numeric_values=_TRIP_RELATIONSHIP_BY_SOURCE_VALUE,
    )


def decode_vehicle_status(value: object) -> HistoricalVehicleStatus:
    """Decode one documented numeric or canonical string vehicle status."""

    return _decode_enum(
        value,
        field="vehicle status",
        enum_type=HistoricalVehicleStatus,
        numeric_values=_VEHICLE_STATUS_BY_SOURCE_VALUE,
    )


def canonical_float(value: float | None) -> str | None:
    """Represent a finite float exactly for deterministic state hashing."""

    if value is None:
        return None
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError("canonical floats must be finite")
    if numeric == 0.0:
        numeric = 0.0
    return numeric.hex()


def _canonical_sha256(value: object) -> str:
    body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(body).hexdigest()


def _require_nonempty(value: str | None, field: str) -> None:
    if value is None or not value:
        raise ValueError(f"{field} must be nonempty")


def _require_gtfs_time(value: str, field: str) -> None:
    pieces = value.split(":")
    if len(pieces) != 3 or not all(piece.isdigit() for piece in pieces):
        raise ValueError(f"{field} must use GTFS HH:MM:SS format")
    hours, minutes, seconds = (int(piece) for piece in pieces)
    if hours < 0 or not 0 <= minutes < 60 or not 0 <= seconds < 60:
        raise ValueError(f"{field} must use GTFS HH:MM:SS format")


@dataclass(frozen=True, slots=True, order=True)
class SourceLineageEntry:
    """One exact input row contributing to a normalized observation."""

    source_object_key: str
    source_row_ordinal: int

    def __post_init__(self) -> None:
        _require_nonempty(self.source_object_key, "source_object_key")
        if (
            isinstance(self.source_row_ordinal, bool)
            or not isinstance(self.source_row_ordinal, int)
            or self.source_row_ordinal < 0
        ):
            raise ValueError("source_row_ordinal must be a nonnegative integer")


def vehicle_observation_id(
    *,
    trip_start_date: date,
    trip_start_time: str,
    trip_id: str,
    route_id: str,
    direction_id: int,
    vehicle_id: str,
    observation_utc: datetime,
    stop_sequence: int | None,
    current_status: HistoricalVehicleStatus,
) -> str:
    """Hash the frozen canonical observation identity tuple."""

    require_utc(observation_utc, "observation_utc")
    return _canonical_sha256(
        [
            trip_start_date.isoformat(),
            trip_start_time,
            trip_id,
            route_id,
            direction_id,
            vehicle_id,
            observation_utc.isoformat(),
            stop_sequence,
            current_status.value,
        ]
    )


@dataclass(frozen=True, slots=True)
class VehicleObservation:
    """One valid, timezone-resolved, canonically identified source observation."""

    observation_id: str
    source_lineage: tuple[SourceLineageEntry, ...]
    entity_id: str | None
    trip_id: str
    trip_start_date: date
    trip_start_time: str
    schedule_relationship: TripScheduleRelationship
    route_id: str
    direction_id: int
    vehicle_id: str
    vehicle_label: str | None
    observation_source_naive_utc: datetime
    observation_utc: datetime
    stop_sequence: int | None
    stop_id: str | None
    current_status: HistoricalVehicleStatus
    latitude: float | None
    longitude: float | None
    bearing: float | None
    speed: float | None
    schema_version: str

    def __post_init__(self) -> None:
        for required_field, required_value in (
            ("trip_id", self.trip_id),
            ("route_id", self.route_id),
            ("vehicle_id", self.vehicle_id),
            ("schema_version", self.schema_version),
        ):
            _require_nonempty(required_value, required_field)
        for optional_field, optional_value in (
            ("entity_id", self.entity_id),
            ("vehicle_label", self.vehicle_label),
            ("stop_id", self.stop_id),
        ):
            if optional_value == "":
                raise ValueError(f"{optional_field} cannot be empty")
        _require_gtfs_time(self.trip_start_time, "trip_start_time")
        if isinstance(self.direction_id, bool) or self.direction_id not in (0, 1):
            raise ValueError("direction_id must be zero or one")
        if self.stop_sequence is not None and (
            isinstance(self.stop_sequence, bool) or self.stop_sequence < 0
        ):
            raise ValueError("stop_sequence must be a nonnegative integer or null")
        if not isinstance(self.schedule_relationship, TripScheduleRelationship):
            raise ValueError("schedule_relationship must be a canonical known enum")
        if not isinstance(self.current_status, HistoricalVehicleStatus):
            raise ValueError("current_status must be a canonical known enum")
        if self.observation_source_naive_utc.tzinfo is not None:
            raise ValueError("observation_source_naive_utc must not have timezone information")
        require_utc(self.observation_utc, "observation_utc")
        if self.observation_source_naive_utc.replace(tzinfo=UTC) != self.observation_utc:
            raise ValueError("observation_utc must attach UTC without clock arithmetic")
        expected_lineage = tuple(
            sorted(
                self.source_lineage,
                key=lambda item: (item.source_object_key.encode(), item.source_row_ordinal),
            )
        )
        if not self.source_lineage or self.source_lineage != expected_lineage:
            raise ValueError("source_lineage must be nonempty and canonically sorted")
        if len(set(self.source_lineage)) != len(self.source_lineage):
            raise ValueError("source_lineage must not contain duplicate rows")
        for numeric_field, numeric_value in (
            ("latitude", self.latitude),
            ("longitude", self.longitude),
            ("bearing", self.bearing),
            ("speed", self.speed),
        ):
            if numeric_value is not None:
                try:
                    canonical_float(numeric_value)
                except ValueError as error:
                    raise ValueError(f"{numeric_field} must be finite") from error
        if self.latitude is not None and not -90 <= self.latitude <= 90:
            raise ValueError("latitude must be between -90 and 90")
        if self.longitude is not None and not -180 <= self.longitude <= 180:
            raise ValueError("longitude must be between -180 and 180")
        if self.bearing is not None and not 0 <= self.bearing < 360:
            raise ValueError("bearing must be in [0, 360)")
        if self.speed is not None and self.speed < 0:
            raise ValueError("speed cannot be negative")
        expected_id = vehicle_observation_id(
            trip_start_date=self.trip_start_date,
            trip_start_time=self.trip_start_time,
            trip_id=self.trip_id,
            route_id=self.route_id,
            direction_id=self.direction_id,
            vehicle_id=self.vehicle_id,
            observation_utc=self.observation_utc,
            stop_sequence=self.stop_sequence,
            current_status=self.current_status,
        )
        if self.observation_id != expected_id:
            raise ValueError("observation_id does not match the canonical identity")

    @property
    def canonical_state_payload(self) -> tuple[Any, ...]:
        """Return the compared duplicate-state payload with exact float encodings."""

        return (
            self.entity_id,
            self.schedule_relationship.value,
            self.stop_id,
            self.vehicle_label,
            canonical_float(self.latitude),
            canonical_float(self.longitude),
            canonical_float(self.bearing),
            canonical_float(self.speed),
        )


def trip_episode_id(
    *,
    service_date: date,
    trip_id: str,
    trip_start_time: str,
    route_id: str,
    direction_id: int,
    vehicle_id: str,
    observation_ids: tuple[str, ...],
) -> str:
    """Hash one deterministic episode identity and its ordered observations."""

    return _canonical_sha256(
        [
            service_date.isoformat(),
            trip_id,
            trip_start_time,
            route_id,
            direction_id,
            vehicle_id,
            list(observation_ids),
        ]
    )


@dataclass(frozen=True, slots=True)
class TripEpisode:
    """One stable train session after canonical ordering and split rules."""

    episode_id: str
    service_date: date
    trip_id: str
    trip_start_time: str
    route_id: str
    direction_id: int
    vehicle_id: str
    first_observation_utc: datetime
    last_observation_utc: datetime
    observation_ids: tuple[str, ...]
    maximum_gap_seconds: float
    schedule_match_status: EpisodeScheduleMatchStatus
    schedule_version_id: str | None
    route_pattern_id: str | None
    quality_flags: tuple[EpisodeQualityFlag, ...]

    def __post_init__(self) -> None:
        for field, value in (
            ("trip_id", self.trip_id),
            ("route_id", self.route_id),
            ("vehicle_id", self.vehicle_id),
        ):
            _require_nonempty(value, field)
        _require_gtfs_time(self.trip_start_time, "trip_start_time")
        if isinstance(self.direction_id, bool) or self.direction_id not in (0, 1):
            raise ValueError("direction_id must be zero or one")
        require_utc(self.first_observation_utc, "first_observation_utc")
        require_utc(self.last_observation_utc, "last_observation_utc")
        if self.last_observation_utc < self.first_observation_utc:
            raise ValueError("episode observation interval is inverted")
        if not self.observation_ids or len(set(self.observation_ids)) != len(self.observation_ids):
            raise ValueError("observation_ids must be nonempty and unique")
        if (
            not math.isfinite(self.maximum_gap_seconds)
            or not 0 <= self.maximum_gap_seconds <= PRIMARY_EPISODE_GAP_SECONDS
        ):
            raise ValueError("maximum_gap_seconds must be finite and no greater than 600")
        if not isinstance(self.schedule_match_status, EpisodeScheduleMatchStatus):
            raise ValueError("schedule_match_status must be a canonical known enum")
        if self.schedule_match_status is EpisodeScheduleMatchStatus.EXACT_MATCH:
            _require_nonempty(self.schedule_version_id, "schedule_version_id")
            _require_nonempty(self.route_pattern_id, "route_pattern_id")
        elif self.schedule_version_id is not None or self.route_pattern_id is not None:
            raise ValueError("unmatched episodes cannot carry schedule match identifiers")
        if not all(isinstance(flag, EpisodeQualityFlag) for flag in self.quality_flags):
            raise ValueError("quality_flags must contain canonical known enums")
        expected_flags = tuple(
            sorted(set(self.quality_flags), key=lambda flag: flag.value.encode())
        )
        if self.quality_flags != expected_flags:
            raise ValueError("quality_flags must be unique and bytewise sorted")
        expected_id = trip_episode_id(
            service_date=self.service_date,
            trip_id=self.trip_id,
            trip_start_time=self.trip_start_time,
            route_id=self.route_id,
            direction_id=self.direction_id,
            vehicle_id=self.vehicle_id,
            observation_ids=self.observation_ids,
        )
        if self.episode_id != expected_id:
            raise ValueError("episode_id does not match the canonical episode identity")


def downstream_example_id(
    *,
    episode_id: str,
    anchor_observation_id: str,
    destination_stop_id: str | None,
    destination_stop_sequence: int | None,
) -> str:
    """Hash the immutable identity of one anchor and destination pair."""

    return _canonical_sha256(
        [episode_id, anchor_observation_id, destination_stop_id, destination_stop_sequence]
    )


@dataclass(frozen=True, slots=True)
class DownstreamStopExample:
    """One bounded downstream stop-observation target from a frozen anchor."""

    example_id: str
    episode_id: str
    service_date: date
    anchor_observation_id: str
    feature_cutoff_utc: datetime
    origin_stop_id: str
    origin_stop_sequence: int
    destination_stop_id: str | None
    destination_stop_sequence: int | None
    destination_offset: int | None
    scheduled_remaining_seconds: int | None
    lower_evidence_observation_id: str | None
    upper_evidence_observation_id: str | None
    lower_bound_seconds: float | None
    upper_bound_seconds: float | None
    outcome_state: DownstreamOutcomeState
    base_weight: float

    def __post_init__(self) -> None:
        for field, value in (
            ("episode_id", self.episode_id),
            ("anchor_observation_id", self.anchor_observation_id),
            ("origin_stop_id", self.origin_stop_id),
        ):
            _require_nonempty(value, field)
        require_utc(self.feature_cutoff_utc, "feature_cutoff_utc")
        if isinstance(self.origin_stop_sequence, bool) or self.origin_stop_sequence < 0:
            raise ValueError("origin_stop_sequence must be a nonnegative integer")
        if not isinstance(self.outcome_state, DownstreamOutcomeState):
            raise ValueError("outcome_state must be a canonical known enum")
        if not math.isfinite(self.base_weight) or not 0 < self.base_weight <= 1:
            raise ValueError("base_weight must be finite and in (0, 1]")

        unmatched = self.outcome_state is DownstreamOutcomeState.SCHEDULE_UNMATCHED
        destination = (
            self.destination_stop_id,
            self.destination_stop_sequence,
            self.destination_offset,
            self.scheduled_remaining_seconds,
        )
        if unmatched:
            if any(value is not None for value in destination):
                raise ValueError("schedule-unmatched examples cannot carry a destination")
        else:
            destination_stop_id = self.destination_stop_id
            destination_stop_sequence = self.destination_stop_sequence
            destination_offset = self.destination_offset
            scheduled_remaining_seconds = self.scheduled_remaining_seconds
            if (
                destination_stop_id is None
                or destination_stop_sequence is None
                or destination_offset is None
                or scheduled_remaining_seconds is None
            ):
                raise ValueError("matched examples require complete destination fields")
            _require_nonempty(destination_stop_id, "destination_stop_id")
            if destination_stop_sequence <= self.origin_stop_sequence:
                raise ValueError("destination sequence must follow the origin sequence")
            if not 1 <= destination_offset <= MAX_DESTINATION_OFFSET:
                raise ValueError("destination_offset must be between one and eight")
            if not 1 <= scheduled_remaining_seconds <= MAX_SCHEDULED_REMAINING_SECONDS:
                raise ValueError("scheduled remaining time must be in (0, 1800]")

        finite_states = {
            DownstreamOutcomeState.INTERVAL_RESOLVED,
            DownstreamOutcomeState.LEFT_CENSORED,
            DownstreamOutcomeState.OVER_WIDTH_INTERVAL,
        }
        excluded_states = {
            DownstreamOutcomeState.MISSING_STOP_OBSERVATION,
            DownstreamOutcomeState.SESSION_DISCONTINUITY,
            DownstreamOutcomeState.SCHEDULE_UNMATCHED,
            DownstreamOutcomeState.NO_FOLLOW_UP,
        }
        if self.outcome_state in finite_states:
            self._validate_finite_bounds()
        elif self.outcome_state is DownstreamOutcomeState.RIGHT_CENSORED:
            self._validate_right_censored_bounds()
        elif self.outcome_state in excluded_states and any(
            value is not None
            for value in (
                self.lower_evidence_observation_id,
                self.upper_evidence_observation_id,
                self.lower_bound_seconds,
                self.upper_bound_seconds,
            )
        ):
            raise ValueError("excluded examples cannot carry arrival bounds or evidence")

        expected_id = downstream_example_id(
            episode_id=self.episode_id,
            anchor_observation_id=self.anchor_observation_id,
            destination_stop_id=self.destination_stop_id,
            destination_stop_sequence=self.destination_stop_sequence,
        )
        if self.example_id != expected_id:
            raise ValueError("example_id does not match the canonical example identity")

    def _validate_finite_bounds(self) -> None:
        if self.lower_bound_seconds is None or self.upper_bound_seconds is None:
            raise ValueError("finite examples require both interval bounds")
        if not math.isfinite(self.lower_bound_seconds) or not math.isfinite(
            self.upper_bound_seconds
        ):
            raise ValueError("finite example bounds must be finite")
        if self.lower_bound_seconds < 0 or self.upper_bound_seconds <= self.lower_bound_seconds:
            raise ValueError("finite example bounds must satisfy 0 <= lower < upper")
        _require_nonempty(self.lower_evidence_observation_id, "lower_evidence_observation_id")
        _require_nonempty(self.upper_evidence_observation_id, "upper_evidence_observation_id")
        width = self.upper_bound_seconds - self.lower_bound_seconds
        if self.outcome_state is DownstreamOutcomeState.INTERVAL_RESOLVED:
            if self.lower_bound_seconds <= 0:
                raise ValueError("interval-resolved examples require a positive lower bound")
            if width > MAX_FINITE_INTERVAL_WIDTH_SECONDS:
                raise ValueError("interval-resolved example exceeds the width limit")
        elif self.outcome_state is DownstreamOutcomeState.LEFT_CENSORED:
            if self.lower_bound_seconds != 0:
                raise ValueError("left-censored examples require a zero lower bound")
            if self.lower_evidence_observation_id != self.anchor_observation_id:
                raise ValueError("left-censored lower evidence must be the anchor")
            if width > MAX_FINITE_INTERVAL_WIDTH_SECONDS:
                raise ValueError("left-censored example exceeds the width limit")
        elif width <= MAX_FINITE_INTERVAL_WIDTH_SECONDS:
            raise ValueError("over-width examples must exceed the width limit")

    def _validate_right_censored_bounds(self) -> None:
        if self.lower_bound_seconds is None or self.upper_bound_seconds is None:
            raise ValueError("right-censored examples require both AFT bounds")
        if (
            not math.isfinite(self.lower_bound_seconds)
            or self.lower_bound_seconds <= 0
            or self.lower_bound_seconds > 3_600
            or self.upper_bound_seconds != math.inf
        ):
            raise ValueError("right-censored bounds must be (0, 3600] and positive infinity")
        _require_nonempty(self.lower_evidence_observation_id, "lower_evidence_observation_id")
        if self.upper_evidence_observation_id is not None:
            raise ValueError("right-censored examples cannot carry upper-event evidence")

    @property
    def included_in_likelihood(self) -> bool:
        """Return whether the frozen target state participates in AFT fitting."""

        return self.outcome_state in {
            DownstreamOutcomeState.INTERVAL_RESOLVED,
            DownstreamOutcomeState.LEFT_CENSORED,
            DownstreamOutcomeState.RIGHT_CENSORED,
        }
