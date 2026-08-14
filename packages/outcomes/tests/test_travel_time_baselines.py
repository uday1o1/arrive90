from __future__ import annotations

import pytest
from arrive90_outcomes.travel_time_baselines import (
    EmpiricalMidpointBaseline,
    EmpiricalMidpointQuery,
    EmpiricalMidpointRow,
    fit_empirical_midpoint_baseline,
    official_scheduled_remaining_seconds,
    three_hour_bucket,
)


def _row(index: int, *, midpoint: float | None = None) -> EmpiricalMidpointRow:
    return EmpiricalMidpointRow(
        anchor_id=f"anchor-{index % 25:02d}",
        route_id="Blue",
        direction_id="0",
        origin_stop_id="origin",
        destination_stop_id="destination",
        destination_offset=2,
        day_type="WEEKDAY",
        time_bucket="06:00-09:00",
        example_id=f"example-{index:03d}",
        midpoint_seconds=float(index + 1 if midpoint is None else midpoint),
        analysis_weight=1.0,
    )


def test_empirical_midpoint_fits_supported_cells_and_round_trips() -> None:
    baseline = fit_empirical_midpoint_baseline(tuple(_row(index) for index in range(100)))
    prediction = baseline.predict(_row(0))
    assert prediction.seconds == 50
    assert prediction.backoff_level == "FULL_CELL"
    restored = EmpiricalMidpointBaseline.from_manifest(baseline.manifest)
    assert restored == baseline
    assert restored.manifest_sha256 == baseline.manifest_sha256


def test_empirical_midpoint_uses_frozen_backoff_and_unavailable_global() -> None:
    baseline = fit_empirical_midpoint_baseline(tuple(_row(index) for index in range(100)))
    fallback = EmpiricalMidpointQuery(
        anchor_id="new-anchor",
        route_id="Blue",
        direction_id="1",
        origin_stop_id="elsewhere",
        destination_stop_id="unknown",
        destination_offset=2,
        day_type="WEEKEND",
        time_bucket="21:00-24:00",
    )
    assert baseline.predict(fallback).backoff_level == "GLOBAL_DESTINATION_OFFSET"
    unavailable = EmpiricalMidpointQuery(
        anchor_id="new-anchor",
        route_id="Blue",
        direction_id="1",
        origin_stop_id="elsewhere",
        destination_stop_id="unknown",
        destination_offset=8,
        day_type="WEEKEND",
        time_bucket="21:00-24:00",
    )
    assert baseline.predict(unavailable).seconds is None


def test_empirical_weighted_median_uses_lower_value_tie_break() -> None:
    baseline = fit_empirical_midpoint_baseline(
        (_row(0, midpoint=10), _row(1, midpoint=20)),
        minimum_finite_examples=1,
        minimum_distinct_anchors=1,
    )
    assert baseline.predict(_row(0)).seconds == 10


def test_schedule_and_time_bucket_contracts_fail_closed() -> None:
    assert official_scheduled_remaining_seconds(60) == 60
    assert three_hour_bucket(0) == "00:00-03:00"
    assert three_hour_bucket(23) == "21:00-24:00"
    with pytest.raises(ValueError, match="positive"):
        official_scheduled_remaining_seconds(0)
    with pytest.raises(ValueError, match="zero through 23"):
        three_hour_bucket(24)
