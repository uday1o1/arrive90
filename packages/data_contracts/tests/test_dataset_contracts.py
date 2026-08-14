from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime

import pytest
from arrive90_data_contracts.dataset import (
    DatasetSplit,
    DestinationClass,
    ObservationGapClass,
    PeakPeriod,
    RetentionAuditRow,
    chronological_split,
    destination_class,
    observation_gap_class,
    peak_period,
    schedule_deviation_class,
    scheduled_remaining_class,
)
from arrive90_data_contracts.travel_time import DownstreamOutcomeState


@pytest.mark.parametrize(
    ("service_date", "expected"),
    [
        (date(2024, 1, 1), DatasetSplit.TRAINING),
        (date(2024, 7, 31), DatasetSplit.TRAINING),
        (date(2024, 8, 1), DatasetSplit.MODEL_VALIDATION),
        (date(2024, 9, 30), DatasetSplit.MODEL_VALIDATION),
        (date(2024, 10, 1), DatasetSplit.CALIBRATION),
        (date(2024, 10, 31), DatasetSplit.CALIBRATION),
        (date(2024, 11, 1), DatasetSplit.FINAL_TEST),
        (date(2024, 12, 31), DatasetSplit.FINAL_TEST),
    ],
)
def test_chronological_split_boundaries(service_date: date, expected: DatasetSplit) -> None:
    assert chronological_split(service_date) is expected


def test_split_and_peak_contracts_fail_closed_at_exact_boundaries() -> None:
    with pytest.raises(ValueError, match="outside"):
        chronological_split(date(2023, 12, 31))
    assert peak_period(datetime(2024, 5, 13, 10, 59, 59, 999999, tzinfo=UTC)) is PeakPeriod.OFF_PEAK
    assert peak_period(datetime(2024, 5, 13, 11, tzinfo=UTC)) is PeakPeriod.PEAK
    assert peak_period(datetime(2024, 5, 13, 13, 59, 59, 999999, tzinfo=UTC)) is PeakPeriod.PEAK
    assert peak_period(datetime(2024, 5, 13, 14, tzinfo=UTC)) is PeakPeriod.OFF_PEAK
    assert peak_period(datetime(2024, 5, 11, 11, tzinfo=UTC)) is PeakPeriod.OFF_PEAK


def test_slice_classifiers_cover_frozen_edges() -> None:
    assert destination_class(1, is_terminal=False) is DestinationClass.IMMEDIATE
    assert destination_class(4, is_terminal=False) is DestinationClass.MEDIUM
    assert destination_class(8, is_terminal=False) is DestinationClass.LONG
    assert destination_class(1, is_terminal=True) is DestinationClass.TERMINAL
    assert scheduled_remaining_class(600).value == "SHORT"
    assert scheduled_remaining_class(601).value == "MEDIUM"
    assert scheduled_remaining_class(1_201).value == "LONG"
    assert schedule_deviation_class(-60).value == "LOW"
    assert schedule_deviation_class(300).value == "TYPICAL"
    assert schedule_deviation_class(301).value == "HIGH"
    assert observation_gap_class(None) is ObservationGapClass.MISSING
    assert observation_gap_class(75) is ObservationGapClass.LOW
    assert observation_gap_class(180) is ObservationGapClass.TYPICAL
    assert observation_gap_class(600) is ObservationGapClass.HIGH


def test_retention_audit_projection_contains_no_duration_bounds() -> None:
    row = RetentionAuditRow(
        example_id="example",
        episode_id="episode",
        anchor_observation_id="anchor",
        service_date=date(2024, 11, 1),
        split=DatasetSplit.FINAL_TEST,
        route_id="Red",
        direction_id=0,
        peak_period=PeakPeriod.OFF_PEAK,
        schedule_match_state="EXACT",
        outcome_state=DownstreamOutcomeState.INTERVAL_RESOLVED,
        interval_width_seconds=60,
        likelihood_eligible=True,
        destination_offset=1,
    )
    assert not hasattr(row, "lower_bound_seconds")
    assert not hasattr(row, "upper_bound_seconds")
    with pytest.raises(ValueError, match="split"):
        replace(row, split=DatasetSplit.TRAINING)
