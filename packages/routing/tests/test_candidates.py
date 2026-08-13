from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from arrive90_data_contracts.candidates import CandidateItinerary, TransitLeg
from arrive90_routing.candidates import (
    canonical_schedule_features,
    deduplicate_and_limit,
    eligible_trip_set_hash,
)

NOW = datetime(2025, 1, 1, 12, tzinfo=UTC)


def _candidate(index: int, *, minutes: int = 10) -> CandidateItinerary:
    leg = TransitLeg(
        f"pattern-{index}",
        "Red",
        0,
        f"trip-{index}",
        "stop-a",
        "station-a",
        "stop-b",
        "station-b",
        NOW + timedelta(minutes=index),
        NOW + timedelta(minutes=index + minutes),
        ("stop-a", "stop-b"),
    )
    return CandidateItinerary((leg,), ())


def test_same_policy_departures_deduplicate_independent_of_response_order() -> None:
    first = _candidate(0)
    later_leg = replace(
        first.legs[0],
        trip_id="later",
        scheduled_departure_utc=NOW + timedelta(minutes=20),
        scheduled_arrival_utc=NOW + timedelta(minutes=30),
    )
    later = CandidateItinerary((later_leg,), ())
    assert deduplicate_and_limit((later, first)) == (first,)
    assert deduplicate_and_limit((first, later)) == (first,)
    assert canonical_schedule_features(first) == canonical_schedule_features(
        deduplicate_and_limit((later, first))[0]
    )


def test_distinct_policies_remain_and_truncation_order_is_frozen() -> None:
    candidates = tuple(_candidate(index) for index in reversed(range(17)))
    normalized = deduplicate_and_limit(candidates)
    assert len(normalized) == 16
    assert [candidate.legs[0].route_pattern_id for candidate in normalized] == [
        f"pattern-{index}" for index in range(16)
    ]
    assert eligible_trip_set_hash(candidates) == eligible_trip_set_hash(reversed(candidates))
    with pytest.raises(ValueError, match="positive"):
        deduplicate_and_limit(candidates, maximum_alternatives=0)
