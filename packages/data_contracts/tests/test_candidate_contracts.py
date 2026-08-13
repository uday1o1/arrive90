from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest
from arrive90_data_contracts.candidates import (
    CandidateItinerary,
    HistoricalBaseQuery,
    HistoricalDeadlineVariant,
    TransitLeg,
)

NOW = datetime(2025, 1, 1, 12, tzinfo=UTC)


def _leg() -> TransitLeg:
    return TransitLeg(
        "pattern-a",
        "Red",
        0,
        "trip-a",
        "stop-a",
        "station-a",
        "stop-b",
        "station-b",
        NOW,
        NOW + timedelta(minutes=10),
        ("stop-a", "stop-b"),
    )


def test_transit_leg_and_candidate_policy_key_ignore_departure_alternatives() -> None:
    leg = _leg()
    later = replace(
        leg,
        trip_id="trip-b",
        scheduled_departure_utc=NOW + timedelta(minutes=30),
        scheduled_arrival_utc=NOW + timedelta(minutes=40),
    )
    first = CandidateItinerary((leg,), ())
    second = CandidateItinerary((later,), ())
    assert first.policy_key == second.policy_key
    assert first.planned_duration_seconds == 600
    assert first.transfer_count == 0
    with pytest.raises(ValueError, match="one or two"):
        CandidateItinerary((), ())
    with pytest.raises(ValueError, match="walk count"):
        CandidateItinerary((leg,), (10,))


def test_leg_and_transfer_contracts_reject_invalid_schedule_shapes() -> None:
    leg = _leg()
    with pytest.raises(ValueError, match="arrival must follow"):
        replace(leg, scheduled_arrival_utc=NOW)
    with pytest.raises(ValueError, match="boarding and alighting"):
        replace(leg, stop_sequence=("stop-a",))
    with pytest.raises(ValueError, match="begin"):
        replace(leg, stop_sequence=("other", "stop-b"))
    with pytest.raises(ValueError, match="end"):
        replace(leg, stop_sequence=("stop-a", "other"))
    second = replace(
        leg,
        route_pattern_id="pattern-b",
        trip_id="trip-b",
        boarding_stop_id="stop-b2",
        boarding_parent_station_id="station-b",
        alighting_stop_id="stop-c",
        alighting_parent_station_id="station-c",
        scheduled_departure_utc=NOW + timedelta(minutes=12),
        scheduled_arrival_utc=NOW + timedelta(minutes=20),
        stop_sequence=("stop-b2", "stop-c"),
    )
    candidate = CandidateItinerary((leg, second), (120,))
    assert candidate.transfer_count == 1
    with pytest.raises(ValueError, match="do not connect"):
        CandidateItinerary((leg, replace(second, boarding_parent_station_id="other")), (0,))
    with pytest.raises(ValueError, match="walk rule"):
        CandidateItinerary((leg, second), (121,))


def test_historical_query_and_deadline_contracts_fail_closed() -> None:
    query = HistoricalBaseQuery(
        "query",
        NOW,
        date(2025, 1, 1),
        "a",
        "b",
        NOW,
        NOW + timedelta(minutes=210),
        "schedule",
        "v1",
        "Red",
        1.0,
        "train",
    )
    assert query.base_query_weight == 1.0
    with pytest.raises(ValueError, match="not ordered"):
        replace(query, ready_at_utc=NOW - timedelta(seconds=1))
    variant = HistoricalDeadlineVariant(
        "variant",
        "query",
        NOW + timedelta(minutes=5),
        5,
        1 / 36,
        "0.90",
        20,
        "a" * 64,
    )
    assert variant.deadline_slack_minutes == 5
    with pytest.raises(ValueError, match="supported grid"):
        replace(variant, deadline_slack_minutes=185)
    with pytest.raises(ValueError, match="five-minute"):
        replace(variant, deadline_slack_minutes=6)
