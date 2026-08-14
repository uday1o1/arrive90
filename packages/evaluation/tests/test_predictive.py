import math

import pytest
from arrive90_evaluation.predictive import (
    PointPredictionRow,
    ResolvedProbabilityRow,
    probability_metrics,
    weighted_mae,
)


def test_probability_metrics_are_weighted_and_disclose_binning() -> None:
    rows = (
        ResolvedProbabilityRow(0.8, True, 2),
        ResolvedProbabilityRow(0.2, False, 1),
    )
    metrics = probability_metrics(rows, bins=5)
    assert metrics.brier_score == pytest.approx(0.04)
    assert metrics.log_loss == pytest.approx(-math.log(0.8))
    assert metrics.expected_calibration_error == pytest.approx(0.2)
    assert metrics.maximum_calibration_error == pytest.approx(0.2)
    assert metrics.bins == 5


def test_log_loss_reports_infinity_instead_of_clipping_a_certain_error() -> None:
    metrics = probability_metrics((ResolvedProbabilityRow(0, True, 1),))
    assert math.isinf(metrics.log_loss)
    with pytest.raises(ValueError, match="invalid"):
        ResolvedProbabilityRow(1.1, True, 1)
    with pytest.raises(ValueError, match="require"):
        probability_metrics((), bins=10)


def test_weighted_mae_is_a_secondary_resolved_point_metric() -> None:
    assert weighted_mae(
        (PointPredictionRow(10, 12, 2), PointPredictionRow(10, 4, 1))
    ) == pytest.approx(10 / 3)
    with pytest.raises(ValueError, match="positive"):
        weighted_mae((PointPredictionRow(1, 1, 0),))
