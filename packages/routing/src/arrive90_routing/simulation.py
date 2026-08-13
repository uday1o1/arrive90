"""Canonical static schedule simulation for audited route policies."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import pairwise

from arrive90_data_contracts.candidates import CandidateItinerary, TransitLeg
from arrive90_data_contracts.realtime import require_utc

from arrive90_routing.audit import AuditRoutePolicy
from arrive90_routing.candidates import deduplicate_and_limit


@dataclass(frozen=True)
class ScheduledCall:
    stop_id: str
    parent_station_id: str
    arrival_utc: datetime
    departure_utc: datetime
    pickup_allowed: bool = True
    drop_off_allowed: bool = True

    def __post_init__(self) -> None:
        require_utc(self.arrival_utc, "arrival_utc")
        require_utc(self.departure_utc, "departure_utc")
        if self.departure_utc < self.arrival_utc:
            raise ValueError("scheduled call departure cannot precede arrival")


@dataclass(frozen=True)
class ScheduledTrip:
    trip_id: str
    route_pattern_id: str
    route_id: str
    direction_id: int
    calls: tuple[ScheduledCall, ...]

    def __post_init__(self) -> None:
        if len(self.calls) < 2:
            raise ValueError("scheduled trip requires at least two calls")
        for earlier, later in pairwise(self.calls):
            if later.arrival_utc < earlier.departure_utc:
                raise ValueError("scheduled trip calls are not chronological")

    def leg(self, stop_ids: tuple[str, ...]) -> TransitLeg | None:
        if len(stop_ids) < 2:
            return None
        for start in range(len(self.calls)):
            end = start + len(stop_ids)
            calls = self.calls[start:end]
            if tuple(call.stop_id for call in calls) != stop_ids:
                continue
            if not calls[0].pickup_allowed or not calls[-1].drop_off_allowed:
                return None
            return TransitLeg(
                route_pattern_id=self.route_pattern_id,
                route_id=self.route_id,
                direction_id=self.direction_id,
                trip_id=self.trip_id,
                boarding_stop_id=calls[0].stop_id,
                boarding_parent_station_id=calls[0].parent_station_id,
                alighting_stop_id=calls[-1].stop_id,
                alighting_parent_station_id=calls[-1].parent_station_id,
                scheduled_departure_utc=calls[0].departure_utc,
                scheduled_arrival_utc=calls[-1].arrival_utc,
                stop_sequence=stop_ids,
            )
        return None


def simulate_policy(
    policy: AuditRoutePolicy,
    trips: tuple[ScheduledTrip, ...],
    *,
    ready_at_utc: datetime,
    departure_window_minutes: int = 90,
    maximum_alternatives: int = 16,
) -> tuple[CandidateItinerary, ...]:
    """Simulate direct or one-transfer static departures for one route policy."""

    require_utc(ready_at_utc, "ready_at_utc")
    if departure_window_minutes <= 0:
        raise ValueError("departure window must be positive")
    window_end = ready_at_utc + timedelta(minutes=departure_window_minutes)
    leg_options: list[list[TransitLeg]] = []
    for pattern_id, route_id, direction_id, stop_ids in policy.legs:
        options: list[TransitLeg] = []
        for trip in trips:
            if (
                trip.route_pattern_id != pattern_id
                or trip.route_id != route_id
                or trip.direction_id != direction_id
            ):
                continue
            leg = trip.leg(stop_ids)
            if leg is not None:
                options.append(leg)
        leg_options.append(options)
    candidates: list[CandidateItinerary] = []
    if len(policy.legs) == 1:
        candidates.extend(
            CandidateItinerary((leg,), ())
            for leg in leg_options[0]
            if ready_at_utc <= leg.scheduled_departure_utc <= window_end
        )
    else:
        walk = policy.transfer_walk_seconds[0]
        for first in leg_options[0]:
            if not ready_at_utc <= first.scheduled_departure_utc <= window_end:
                continue
            for second in leg_options[1]:
                if second.scheduled_departure_utc < first.scheduled_arrival_utc + timedelta(
                    seconds=walk
                ):
                    continue
                candidates.append(CandidateItinerary((first, second), (walk,)))
    return deduplicate_and_limit(candidates, maximum_alternatives=maximum_alternatives)
