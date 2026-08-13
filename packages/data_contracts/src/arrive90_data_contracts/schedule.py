"""Point-in-time schedule, vehicle evidence, and alert revision contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from arrive90_data_contracts.realtime import FeedType, require_utc


class IntervalClosure(StrEnum):
    LEFT_OPEN_RIGHT_CLOSED = "LEFT_OPEN_RIGHT_CLOSED"
    EXACT = "EXACT"
    UNKNOWN = "UNKNOWN"


class ArrivalEvidence(StrEnum):
    VP_STOPPED_AT = "VP_STOPPED_AT"
    VP_DEPARTED_STATION_UPPER_BOUND = "VP_DEPARTED_STATION_UPPER_BOUND"
    VERIFIED_PAST_TRIP_UPDATE = "VERIFIED_PAST_TRIP_UPDATE"
    PREDICTED_TRIP_UPDATE = "PREDICTED_TRIP_UPDATE"
    UNKNOWN = "UNKNOWN"


class DepartureEvidence(StrEnum):
    DIRECT_DEPARTURE = "DIRECT_DEPARTURE"
    DOWNSTREAM_MOVE_UPPER_BOUND = "DOWNSTREAM_MOVE_UPPER_BOUND"
    UNKNOWN = "UNKNOWN"


class VehicleStatus(StrEnum):
    INCOMING_AT = "INCOMING_AT"
    STOPPED_AT = "STOPPED_AT"
    IN_TRANSIT_TO = "IN_TRANSIT_TO"
    UNKNOWN = "UNKNOWN"


class AlertEffect(StrEnum):
    NO_SERVICE = "NO_SERVICE"
    REDUCED_SERVICE = "REDUCED_SERVICE"
    SIGNIFICANT_DELAYS = "SIGNIFICANT_DELAYS"
    DETOUR = "DETOUR"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ScheduleStopTime:
    schedule_version_id: str
    feed_version: str
    published_at_utc: datetime | None
    known_at_utc: datetime
    active_start_date: date
    active_end_date: date
    service_date: date
    service_id: str
    route_id: str
    direction_id: int
    trip_id: str
    block_id: str | None
    stop_id: str
    parent_station_id: str
    stop_sequence: int
    scheduled_arrival_local_seconds: int
    scheduled_departure_local_seconds: int
    pickup_type: int
    drop_off_type: int
    wheelchair_accessibility: int

    def __post_init__(self) -> None:
        require_utc(self.known_at_utc, "known_at_utc")
        if self.published_at_utc is not None:
            require_utc(self.published_at_utc, "published_at_utc")
            if self.known_at_utc < self.published_at_utc:
                raise ValueError("schedule cannot be known before its publication evidence")
        if not self.active_start_date <= self.service_date <= self.active_end_date:
            raise ValueError("service date must be inside the schedule active interval")
        if self.stop_sequence < 0:
            raise ValueError("stop_sequence cannot be negative")
        if (
            min(
                self.scheduled_arrival_local_seconds,
                self.scheduled_departure_local_seconds,
            )
            < 0
        ):
            raise ValueError("scheduled GTFS seconds cannot be negative")
        if self.scheduled_departure_local_seconds < self.scheduled_arrival_local_seconds:
            raise ValueError("scheduled departure cannot precede scheduled arrival")


@dataclass(frozen=True)
class PrimitiveStopObservation:
    source_row_key: str
    feed_type: FeedType
    observed_trip_id: str
    route_id: str
    direction_id: int
    stop_id: str
    parent_station_id: str
    stop_sequence: int
    vehicle_status: VehicleStatus
    event_time_utc: datetime
    source_observed_at_utc: datetime | None
    pipeline_known_at_utc: datetime
    product_available_at_utc: datetime
    is_prediction: bool = False
    previous_stop_id: str | None = None

    def __post_init__(self) -> None:
        for field, value in (
            ("event_time_utc", self.event_time_utc),
            ("pipeline_known_at_utc", self.pipeline_known_at_utc),
            ("product_available_at_utc", self.product_available_at_utc),
        ):
            require_utc(value, field)
        if self.source_observed_at_utc is not None:
            require_utc(self.source_observed_at_utc, "source_observed_at_utc")
        ordered = [self.event_time_utc]
        if self.source_observed_at_utc is not None:
            ordered.append(self.source_observed_at_utc)
        ordered.extend((self.pipeline_known_at_utc, self.product_available_at_utc))
        if ordered != sorted(ordered):
            raise ValueError("observation temporal lineage is not monotonic")


@dataclass(frozen=True)
class NormalizedStopEvidence:
    source_row_key: str
    observed_trip_id: str
    stop_id: str
    stop_sequence: int
    arrival_lower_bound_utc: datetime | None
    arrival_upper_bound_utc: datetime | None
    arrival_interval_closed: IntervalClosure
    arrival_evidence: ArrivalEvidence
    departure_upper_bound_utc: datetime | None
    departure_evidence: DepartureEvidence
    product_available_at_utc: datetime
    usable_for_primary_boarding: bool

    def __post_init__(self) -> None:
        require_utc(self.product_available_at_utc, "product_available_at_utc")
        for field, value in (
            ("arrival_lower_bound_utc", self.arrival_lower_bound_utc),
            ("arrival_upper_bound_utc", self.arrival_upper_bound_utc),
            ("departure_upper_bound_utc", self.departure_upper_bound_utc),
        ):
            if value is not None:
                require_utc(value, field)
        if (
            self.usable_for_primary_boarding
            and self.arrival_evidence is not ArrivalEvidence.VP_STOPPED_AT
        ):
            raise ValueError("only direct Vehicle Position stop evidence can support boarding")


@dataclass(frozen=True)
class AlertRevision:
    alert_id: str
    revision_number: int
    source_attempt_id: str
    source_header_timestamp: datetime | None
    product_available_at_utc: datetime
    active_start_utc: datetime | None
    active_end_utc: datetime | None
    informed_entity_keys: tuple[str, ...]
    effect: AlertEffect
    text_sha256: str

    def __post_init__(self) -> None:
        if self.revision_number < 1:
            raise ValueError("alert revision number must be positive")
        for field, value in (
            ("source_header_timestamp", self.source_header_timestamp),
            ("product_available_at_utc", self.product_available_at_utc),
            ("active_start_utc", self.active_start_utc),
            ("active_end_utc", self.active_end_utc),
        ):
            if value is not None:
                require_utc(value, field)
        if (
            self.active_start_utc is not None
            and self.active_end_utc is not None
            and self.active_end_utc <= self.active_start_utc
        ):
            raise ValueError("alert active interval must be increasing")
        if tuple(sorted(set(self.informed_entity_keys))) != self.informed_entity_keys:
            raise ValueError("alert informed entities must be unique and sorted")
        if len(self.text_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.text_sha256
        ):
            raise ValueError("text_sha256 must be lowercase hexadecimal SHA-256")
