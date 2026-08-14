from decimal import Decimal

import pytest
from arrive90_evaluation.metrics import (
    PolicyPairRow,
    PredictionRow,
    QuantileRow,
    TransferPredictionRow,
    calibration_by_deadline_band,
    calibration_summary,
    deadline_band_id,
    policy_outcome_bounds,
    policy_pair_summary,
    quantile_summary,
    resolution_rates_by_slice,
    transfer_calibration_by_decile,
    transfer_decile_id,
    transfer_station_summary,
)


def test_frozen_deadline_band_boundaries_are_left_closed_and_final_right_closed() -> None:
    assert deadline_band_id(Decimal("0.000000")) == "[0.00,0.10)"
    assert deadline_band_id(Decimal("0.099999")) == "[0.00,0.10)"
    assert deadline_band_id(Decimal("0.100000")) == "[0.10,0.20)"
    assert deadline_band_id(Decimal("0.950000")) == "[0.95,1.00]"
    assert deadline_band_id(Decimal("1.000000")) == "[0.95,1.00]"
    with pytest.raises(ValueError, match="frozen"):
        deadline_band_id(Decimal("1.1"))


def test_complete_population_calibration_keeps_unresolved_rows() -> None:
    rows = (
        PredictionRow("d1", "q1", "day-1", 1, Decimal("0.8"), 0.81, True),
        PredictionRow("d2", "q2", "day-1", 1, Decimal("0.8"), 0.79, False),
        PredictionRow("d3", "q3", "day-2", 2, Decimal("0.8"), 0.80, None),
    )
    summary = calibration_summary(rows)
    assert summary.predicted_mean == pytest.approx(0.8)
    assert summary.success_lower == 0.25
    assert summary.success_upper == 0.75
    assert summary.worst_case_absolute_gap == pytest.approx(0.55)
    assert summary.decision_count == 3
    assert summary.resolved_count == 2
    assert summary.distinct_base_queries == 3
    assert summary.service_day_blocks == 2
    assert summary.resolved_weighted_mass == 2
    assert summary.cluster_adjusted_effective_sample_size == pytest.approx(2.0)
    with pytest.raises(ValueError, match="empty"):
        calibration_summary(())
    with pytest.raises(ValueError, match="positive"):
        PredictionRow("d", "q", "day", 0, Decimal("0.8"), 0.8, True)
    grouped = calibration_by_deadline_band(
        (*rows, PredictionRow("d4", "q4", "day-2", 1, Decimal("0.95"), 0.96, True))
    )
    assert [band for band, _summary in grouped] == ["[0.80,0.90)", "[0.95,1.00]"]


def test_policy_pair_summary_uses_exact_partial_identification_formulas() -> None:
    rows = (
        PolicyPairRow("v1", "q1", "day-1", 1, True, False, 60),
        PolicyPairRow("v2", "q2", "day-1", 1, None, True, 600),
        PolicyPairRow("v3", "q3", "day-2", 1, False, None, 1_200),
        PolicyPairRow("v4", "q4", "day-2", 1, None, None, None),
    )
    summary = policy_pair_summary(rows)
    assert summary.difference_lower == pytest.approx(-0.5)
    assert summary.difference_upper == pytest.approx(0.5)
    assert summary.paired_resolved_estimate == 1.0
    assert summary.pair_resolution_rate == 0.25
    assert summary.mean_added_planned_time_seconds == 620
    assert summary.p95_added_planned_time_seconds == 1_200
    assert summary.maximum_added_planned_time_seconds == 1_200
    arrive90_bounds = policy_outcome_bounds(rows, policy="arrive90")
    assert arrive90_bounds.success_lower == 0.25
    assert arrive90_bounds.success_upper == 0.75
    assert arrive90_bounds.resolved_rate == 0.5
    comparator_bounds = policy_outcome_bounds(rows, policy="comparator")
    assert comparator_bounds.success_lower == 0.25
    assert comparator_bounds.success_upper == 0.75
    with pytest.raises(ValueError, match="named policy"):
        policy_outcome_bounds(rows, policy="unknown")
    rates = resolution_rates_by_slice(
        tuple(
            PolicyPairRow(
                row.variant_id,
                row.base_query_id,
                row.service_day,
                row.weight,
                row.arrive90_success,
                row.comparator_success,
                row.added_planned_time_seconds,
                ("red-peak",),
            )
            for row in rows
        )
    )
    assert rates == {"OVERALL": 0.25, "red-peak": 0.25}
    with pytest.raises(ValueError, match="empty"):
        policy_pair_summary(())
    with pytest.raises(ValueError, match="empty"):
        resolution_rates_by_slice(())


def test_quantile_coverage_and_pinball_bounds_report_excluded_censoring() -> None:
    rows = (
        QuantileRow("day-1", 1, 0.9, 10, 5, 8),
        QuantileRow("day-1", 1, 0.9, 10, 9, 12),
        QuantileRow("day-2", 2, 0.9, 10, None, None),
    )
    summary = quantile_summary(rows)
    assert summary.coverage_lower == 0.25
    assert summary.coverage_upper == 1.0
    assert summary.worst_case_coverage_gap == pytest.approx(0.65)
    assert summary.pinball_loss_lower == pytest.approx(0.1)
    assert summary.pinball_loss_upper == pytest.approx(1.15)
    assert summary.finite_weighted_mass == 2
    assert summary.excluded_censored_weighted_mass == 2
    with pytest.raises(ValueError, match="mix"):
        quantile_summary((rows[0], QuantileRow("day", 1, 0.5, 10, 5, 8)))


def test_no_finite_quantile_intervals_has_null_conditional_loss() -> None:
    summary = quantile_summary((QuantileRow("day", 1, 0.5, 10, None, None),))
    assert summary.pinball_loss_lower is None
    assert summary.pinball_loss_upper is None
    with pytest.raises(ValueError, match="empty"):
        quantile_summary(())


def test_transfer_deciles_and_station_bound_keep_unresolved_rows() -> None:
    assert transfer_decile_id(Decimal("0.000000")) == "[0.0,0.1)"
    assert transfer_decile_id(Decimal("0.100000")) == "[0.1,0.2)"
    assert transfer_decile_id(Decimal("1.000000")) == "[0.9,1.0]"
    rows = (
        TransferPredictionRow("t1", "q1", "day-1", "park", 1, Decimal("0.4"), 0.4, True),
        TransferPredictionRow("t2", "q2", "day-2", "park", 1, Decimal("0.4"), 0.4, None),
        TransferPredictionRow("t3", "q3", "day-2", "park", 2, Decimal("0.8"), 0.8, False),
    )
    deciles = dict(transfer_calibration_by_decile(rows))
    assert deciles["[0.4,0.5)"].success_lower == 0.5
    assert deciles["[0.4,0.5)"].success_upper == 1.0
    station = transfer_station_summary(rows)
    assert station.expected_calibration_bound == pytest.approx(0.7)
    assert station.weighted_mass == 4
    assert station.resolved_count == 2
    with pytest.raises(ValueError, match="frozen"):
        transfer_decile_id(Decimal("1.1"))
    with pytest.raises(ValueError, match="one nonempty"):
        transfer_station_summary(
            (
                *rows,
                TransferPredictionRow("t4", "q4", "day-3", "other", 1, Decimal("0.5"), 0.5, True),
            )
        )
