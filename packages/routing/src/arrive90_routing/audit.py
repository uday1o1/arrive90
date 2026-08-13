"""Small static enumerator used only to audit bounded router recall."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class PlatformCall:
    stop_id: str
    parent_station_id: str


@dataclass(frozen=True)
class RoutePattern:
    route_pattern_id: str
    route_id: str
    direction_id: int
    calls: tuple[PlatformCall, ...]

    def __post_init__(self) -> None:
        if len(self.calls) < 2:
            raise ValueError("route pattern must contain at least two calls")
        if len({call.stop_id for call in self.calls}) != len(self.calls):
            raise ValueError("route pattern cannot repeat a platform in V1")

    def segment(self, origin: str, destination: str) -> tuple[PlatformCall, ...] | None:
        origin_indexes = [
            index for index, call in enumerate(self.calls) if call.parent_station_id == origin
        ]
        destination_indexes = [
            index for index, call in enumerate(self.calls) if call.parent_station_id == destination
        ]
        pairs = [
            (origin_index, destination_index)
            for origin_index in origin_indexes
            for destination_index in destination_indexes
            if origin_index < destination_index
        ]
        if not pairs:
            return None
        start, end = min(pairs)
        return self.calls[start : end + 1]


@dataclass(frozen=True)
class TransferWalkRule:
    from_stop_id: str
    to_stop_id: str
    parent_station_id: str
    duration_seconds: int

    def __post_init__(self) -> None:
        if self.duration_seconds < 0:
            raise ValueError("transfer walk duration cannot be negative")


@dataclass(frozen=True)
class AuditRoutePolicy:
    legs: tuple[tuple[str, str, int, tuple[str, ...]], ...]
    transfer_walk_seconds: tuple[int, ...]

    @property
    def policy_key(self) -> str:
        components: list[str] = ["STATIC_ROUTE_POLICY_V1"]
        for index, (pattern, route, direction, stops) in enumerate(self.legs):
            components.extend((str(index), pattern, route, str(direction), *stops))
            if index < len(self.transfer_walk_seconds):
                components.append(str(self.transfer_walk_seconds[index]))
        return hashlib.sha256("\0".join(components).encode()).hexdigest()


class StaticAuditEnumerator:
    """Enumerate simple direct and one-transfer policies without journey outcomes."""

    def __init__(
        self, patterns: tuple[RoutePattern, ...], transfer_rules: tuple[TransferWalkRule, ...]
    ) -> None:
        self.patterns = tuple(
            sorted(patterns, key=lambda pattern: pattern.route_pattern_id.encode())
        )
        self.rules = {
            (rule.from_stop_id, rule.to_stop_id, rule.parent_station_id): rule.duration_seconds
            for rule in transfer_rules
        }

    @staticmethod
    def _leg(
        pattern: RoutePattern, calls: tuple[PlatformCall, ...]
    ) -> tuple[str, str, int, tuple[str, ...]]:
        return (
            pattern.route_pattern_id,
            pattern.route_id,
            pattern.direction_id,
            tuple(call.stop_id for call in calls),
        )

    def enumerate(self, origin: str, destination: str) -> tuple[AuditRoutePolicy, ...]:
        policies: dict[str, AuditRoutePolicy] = {}
        for pattern in self.patterns:
            segment = pattern.segment(origin, destination)
            if segment is not None:
                policy = AuditRoutePolicy((self._leg(pattern, segment),), ())
                policies[policy.policy_key] = policy
        for first in self.patterns:
            for second in self.patterns:
                if first.route_pattern_id == second.route_pattern_id:
                    continue
                shared = sorted(
                    {call.parent_station_id for call in first.calls}.intersection(
                        call.parent_station_id for call in second.calls
                    ),
                    key=str.encode,
                )
                for transfer in shared:
                    first_segment = first.segment(origin, transfer)
                    second_segment = second.segment(transfer, destination)
                    if first_segment is None or second_segment is None:
                        continue
                    from_stop = first_segment[-1].stop_id
                    to_stop = second_segment[0].stop_id
                    walk = (
                        0
                        if from_stop == to_stop
                        else self.rules.get((from_stop, to_stop, transfer))
                    )
                    if walk is None:
                        continue
                    policy = AuditRoutePolicy(
                        (self._leg(first, first_segment), self._leg(second, second_segment)),
                        (walk,),
                    )
                    policies[policy.policy_key] = policy
        return tuple(policies[key] for key in sorted(policies, key=str.encode))
