from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from arrive90_service.contracts import NormalizedJourneyRequest
from arrive90_service.normalization import normalize_initial_request

NOW = datetime(2025, 1, 1, 12, tzinfo=UTC)
STATIONS = frozenset({"a", "b"})


def _normalize(
    ready_delta: timedelta = timedelta(0),
    deadline_delta: timedelta = timedelta(minutes=30),
) -> NormalizedJourneyRequest:
    return normalize_initial_request(
        origin_station_id="a",
        destination_station_id="b",
        requested_ready_at_utc=NOW + ready_delta,
        requested_deadline_at_utc=NOW + ready_delta + deadline_delta,
        reliability_target=Decimal("0.90"),
        maximum_extra_minutes=20,
        initial_query_cutoff_utc=NOW,
        supported_station_ids=STATIONS,
    )


def test_past_ready_and_deadline_are_conservatively_normalized() -> None:
    result = _normalize(timedelta(minutes=-1), timedelta(minutes=31, seconds=30))
    assert result.effective_ready_at_utc == NOW
    assert result.effective_deadline_at_utc == NOW + timedelta(minutes=30)
    assert result.ready_time_status == "NORMALIZED_TO_CUTOFF"
    assert result.deadline_time_status == "NORMALIZED_DOWN_TO_SUPPORTED_GRID"
    assert result.limitations == (
        "READY_TIME_NORMALIZED_TO_CUTOFF",
        "DEADLINE_NORMALIZED_DOWN_TO_SUPPORTED_GRID",
    )


def test_exact_grid_is_unchanged() -> None:
    result = _normalize()
    assert result.ready_time_status == "AS_REQUESTED"
    assert result.deadline_time_status == "AS_REQUESTED"
    assert result.limitations == ()


@pytest.mark.parametrize(
    ("ready_delta", "deadline_delta", "message"),
    [
        (timedelta(minutes=-2, seconds=-1), timedelta(minutes=30), "ready time"),
        (timedelta(hours=24, seconds=1), timedelta(minutes=30), "ready time"),
        (timedelta(), timedelta(minutes=4, seconds=59), "deadline"),
        (timedelta(), timedelta(minutes=181), "deadline"),
    ],
)
def test_time_bounds_fail_before_backend_work(
    ready_delta: timedelta,
    deadline_delta: timedelta,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _normalize(ready_delta, deadline_delta)


def test_scope_target_and_cap_validation() -> None:
    base = {
        "origin_station_id": "a",
        "destination_station_id": "b",
        "requested_ready_at_utc": NOW,
        "requested_deadline_at_utc": NOW + timedelta(minutes=30),
        "reliability_target": Decimal("0.90"),
        "maximum_extra_minutes": 20,
        "initial_query_cutoff_utc": NOW,
        "supported_station_ids": STATIONS,
    }
    for changes, message in (
        ({"destination_station_id": "a"}, "distinct"),
        ({"destination_station_id": "outside"}, "supported scope"),
        ({"reliability_target": Decimal("0.85")}, "target"),
        ({"maximum_extra_minutes": 21}, "extra time"),
    ):
        with pytest.raises(ValueError, match=message):
            normalize_initial_request(**(base | changes))  # type: ignore[arg-type]
