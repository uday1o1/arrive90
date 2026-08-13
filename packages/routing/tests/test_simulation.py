from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from arrive90_routing.audit import AuditRoutePolicy
from arrive90_routing.simulation import ScheduledCall, ScheduledTrip, simulate_policy

NOW = datetime(2025, 1, 1, 12, tzinfo=UTC)


def _trip(
    trip_id: str,
    pattern: str,
    route: str,
    stop_ids: tuple[str, ...],
    stations: tuple[str, ...],
    offset: int,
) -> ScheduledTrip:
    calls = tuple(
        ScheduledCall(
            stop,
            station,
            NOW + timedelta(minutes=offset + index * 5),
            NOW + timedelta(minutes=offset + index * 5, seconds=30),
        )
        for index, (stop, station) in enumerate(zip(stop_ids, stations, strict=True))
    )
    return ScheduledTrip(trip_id, pattern, route, 0, calls)


def test_canonical_simulation_deduplicates_departures_for_same_policy() -> None:
    policy = AuditRoutePolicy((("red", "Red", 0, ("a", "b")),), ())
    later = _trip("later", "red", "Red", ("a", "b"), ("A", "B"), 20)
    first = _trip("first", "red", "Red", ("a", "b"), ("A", "B"), 1)
    result = simulate_policy(policy, (later, first), ready_at_utc=NOW)
    assert len(result) == 1
    assert result[0].legs[0].trip_id == "first"


def test_canonical_simulation_applies_transfer_walk_and_departure_window() -> None:
    policy = AuditRoutePolicy(
        (
            ("red", "Red", 0, ("a", "x-red")),
            ("orange", "Orange", 0, ("x-orange", "c")),
        ),
        (180,),
    )
    first = _trip("first", "red", "Red", ("a", "x-red"), ("A", "X"), 1)
    too_soon = _trip("too-soon", "orange", "Orange", ("x-orange", "c"), ("X", "C"), 8)
    eligible = _trip("eligible", "orange", "Orange", ("x-orange", "c"), ("X", "C"), 10)
    result = simulate_policy(policy, (too_soon, eligible, first), ready_at_utc=NOW)
    assert len(result) == 1
    assert [leg.trip_id for leg in result[0].legs] == ["first", "eligible"]
    assert simulate_policy(policy, (first, eligible), ready_at_utc=NOW + timedelta(hours=2)) == ()


def test_schedule_simulation_rejects_invalid_calls_and_pickup() -> None:
    call = ScheduledCall("a", "A", NOW, NOW)
    with pytest.raises(ValueError, match="cannot precede"):
        replace(call, departure_utc=NOW - timedelta(seconds=1))
    with pytest.raises(ValueError, match="at least two"):
        ScheduledTrip("bad", "p", "r", 0, (call,))
    trip = _trip("trip", "red", "Red", ("a", "b"), ("A", "B"), 0)
    no_pickup = replace(trip, calls=(replace(trip.calls[0], pickup_allowed=False), trip.calls[1]))
    policy = AuditRoutePolicy((("red", "Red", 0, ("a", "b")),), ())
    assert simulate_policy(policy, (no_pickup,), ready_at_utc=NOW) == ()
    with pytest.raises(ValueError, match="positive"):
        simulate_policy(policy, (trip,), ready_at_utc=NOW, departure_window_minutes=0)
