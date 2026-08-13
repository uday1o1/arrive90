"""Stable candidate itinerary and historical query contracts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime
from itertools import pairwise

from arrive90_data_contracts.realtime import require_utc


def _length_prefixed(values: tuple[str, ...]) -> bytes:
    output = bytearray()
    for value in values:
        encoded = value.encode("utf-8")
        output.extend(len(encoded).to_bytes(4, "big"))
        output.extend(encoded)
    return bytes(output)


@dataclass(frozen=True)
class TransitLeg:
    route_pattern_id: str
    route_id: str
    direction_id: int
    trip_id: str
    boarding_stop_id: str
    boarding_parent_station_id: str
    alighting_stop_id: str
    alighting_parent_station_id: str
    scheduled_departure_utc: datetime
    scheduled_arrival_utc: datetime
    stop_sequence: tuple[str, ...]

    def __post_init__(self) -> None:
        require_utc(self.scheduled_departure_utc, "scheduled_departure_utc")
        require_utc(self.scheduled_arrival_utc, "scheduled_arrival_utc")
        if self.scheduled_arrival_utc <= self.scheduled_departure_utc:
            raise ValueError("transit leg arrival must follow departure")
        if len(self.stop_sequence) < 2:
            raise ValueError("transit leg must contain boarding and alighting stops")
        if self.stop_sequence[0] != self.boarding_stop_id:
            raise ValueError("transit leg stop sequence must begin at boarding stop")
        if self.stop_sequence[-1] != self.alighting_stop_id:
            raise ValueError("transit leg stop sequence must end at alighting stop")

    def policy_components(self) -> tuple[str, ...]:
        return (
            self.route_pattern_id,
            self.route_id,
            str(self.direction_id),
            self.boarding_stop_id,
            self.boarding_parent_station_id,
            self.alighting_stop_id,
            self.alighting_parent_station_id,
            *self.stop_sequence,
        )


@dataclass(frozen=True)
class CandidateItinerary:
    legs: tuple[TransitLeg, ...]
    transfer_walk_seconds: tuple[int, ...]
    generator_version: str = "STATIC_ROUTE_POLICY_V1"

    def __post_init__(self) -> None:
        if not 1 <= len(self.legs) <= 2:
            raise ValueError("candidate must contain one or two transit legs")
        if len(self.transfer_walk_seconds) != len(self.legs) - 1:
            raise ValueError("candidate transfer walk count does not match its legs")
        if any(seconds < 0 for seconds in self.transfer_walk_seconds):
            raise ValueError("transfer walk duration cannot be negative")
        for (first, second), walk in zip(
            pairwise(self.legs), self.transfer_walk_seconds, strict=True
        ):
            if first.alighting_parent_station_id != second.boarding_parent_station_id:
                raise ValueError("candidate transfer parent stations do not connect")
            if second.scheduled_departure_utc < first.scheduled_arrival_utc:
                raise ValueError("candidate transfer departs before the prior leg arrives")
            if (
                second.scheduled_departure_utc - first.scheduled_arrival_utc
            ).total_seconds() < walk:
                raise ValueError("candidate transfer does not satisfy the walk rule")

    @property
    def scheduled_departure_utc(self) -> datetime:
        return self.legs[0].scheduled_departure_utc

    @property
    def scheduled_arrival_utc(self) -> datetime:
        return self.legs[-1].scheduled_arrival_utc

    @property
    def planned_duration_seconds(self) -> int:
        return int((self.scheduled_arrival_utc - self.scheduled_departure_utc).total_seconds())

    @property
    def transfer_count(self) -> int:
        return len(self.legs) - 1

    @property
    def route_pattern_tuple(self) -> tuple[str, ...]:
        return tuple(leg.route_pattern_id for leg in self.legs)

    @property
    def platform_stop_tuple(self) -> tuple[str, ...]:
        return tuple(
            item for leg in self.legs for item in (leg.boarding_stop_id, leg.alighting_stop_id)
        )

    @property
    def policy_key(self) -> str:
        components: tuple[str, ...] = (self.generator_version, str(len(self.legs)))
        for index, leg in enumerate(self.legs):
            components += (f"leg:{index}", *leg.policy_components())
            if index < len(self.transfer_walk_seconds):
                components += (f"walk:{self.transfer_walk_seconds[index]}",)
        return hashlib.sha256(_length_prefixed(components)).hexdigest()


@dataclass(frozen=True)
class HistoricalBaseQuery:
    query_id: str
    query_time_utc: datetime
    service_date: date
    origin_station_id: str
    destination_station_id: str
    ready_at_utc: datetime
    observation_horizon_utc: datetime
    schedule_version_id: str
    query_generation_version: str
    sampling_stratum: str
    base_query_weight: float
    chronological_split: str

    def __post_init__(self) -> None:
        for field, value in (
            ("query_time_utc", self.query_time_utc),
            ("ready_at_utc", self.ready_at_utc),
            ("observation_horizon_utc", self.observation_horizon_utc),
        ):
            require_utc(value, field)
        if not self.query_time_utc <= self.ready_at_utc < self.observation_horizon_utc:
            raise ValueError("historical query timestamps are not ordered")
        if self.base_query_weight <= 0:
            raise ValueError("base query weight must be positive")


@dataclass(frozen=True)
class HistoricalDeadlineVariant:
    variant_id: str
    base_query_id: str
    deadline_utc: datetime
    deadline_slack_minutes: int
    variant_weight: float
    assigned_reliability_target: str
    assigned_maximum_extra_time_minutes: int
    assignment_digest: str

    def __post_init__(self) -> None:
        require_utc(self.deadline_utc, "deadline_utc")
        if not 5 <= self.deadline_slack_minutes <= 180:
            raise ValueError("deadline slack is outside the supported grid")
        if self.deadline_slack_minutes % 5:
            raise ValueError("deadline slack must be a five-minute increment")
        if self.variant_weight <= 0:
            raise ValueError("deadline variant weight must be positive")
