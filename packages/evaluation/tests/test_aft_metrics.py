from __future__ import annotations

import math

import numpy as np
import pytest
from arrive90_evaluation.aft_metrics import (
    aft_cdf_matrix,
    identified_threshold,
    validation_diagnostics,
    weighted_interval_nll,
)
from arrive90_models.distributions import AftDistribution


def test_likelihood_fixtures_cover_exact_interval_left_and_right_censoring() -> None:
    margins = np.full(4, math.log(100), dtype=np.float64)
    lower = np.asarray([100, 50, 0, 100], dtype=np.float64)
    upper = np.asarray([100, 150, 100, math.inf], dtype=np.float64)
    weights = np.ones(4, dtype=np.float64)
    observed = weighted_interval_nll(
        margins,
        lower,
        upper,
        weights,
        scale=1.0,
        distribution=AftDistribution.LOGISTIC,
    )
    exact_likelihood = 0.25 / 100
    interval_likelihood = (1.5 / 2.5) - (0.5 / 1.5)
    expected = -statistics_mean_log((exact_likelihood, interval_likelihood, 0.5, 0.5))
    assert observed == pytest.approx(expected)


def statistics_mean_log(values: tuple[float, ...]) -> float:
    return sum(math.log(value) for value in values) / len(values)


@pytest.mark.parametrize("distribution", tuple(AftDistribution))
def test_cdf_matrix_is_bounded_and_monotonic(distribution: AftDistribution) -> None:
    matrix = aft_cdf_matrix(
        np.asarray([math.log(600), math.log(1200)]),
        (300, 600, 900, 3600),
        scale=0.5,
        distribution=distribution,
    )
    assert matrix.shape == (2, 4)
    assert np.all((matrix >= 0) & (matrix <= 1))
    assert np.all(matrix[:, 1:] >= matrix[:, :-1])


def test_identified_threshold_contract_distinguishes_unresolved_rows() -> None:
    assert identified_threshold(0, 100, 100) is True
    assert identified_threshold(101, math.inf, 100) is False
    assert identified_threshold(50, 150, 100) is None


def test_validation_diagnostics_use_identified_horizons_and_supported_bins() -> None:
    row_count = 240
    diagnostics = validation_diagnostics(
        example_ids=tuple(f"example-{index:03d}" for index in range(row_count)),
        anchor_ids=tuple(f"anchor-{index:03d}" for index in range(row_count)),
        raw_margins=np.linspace(math.log(200), math.log(1000), row_count),
        lower_bounds=np.linspace(100, 600, row_count),
        upper_bounds=np.linspace(150, 700, row_count),
        weights=np.ones(row_count),
        horizons_seconds=(300, 600, 900),
        scale=1.0,
        distribution=AftDistribution.NORMAL,
        minimum_calibration_anchors=20,
    )
    assert diagnostics.weighted_interval_negative_log_likelihood > 0
    assert 0 <= diagnostics.weighted_horizon_brier_score <= 1
    assert 0 <= diagnostics.worst_supported_horizon_calibration_error <= 1
    assert all(item.identified_row_count > 0 for item in diagnostics.horizons)


def test_likelihood_rejects_invalid_bounds() -> None:
    values = np.ones(1)
    with pytest.raises(ValueError, match="bounds or weights"):
        weighted_interval_nll(
            values,
            np.asarray([-1.0]),
            values,
            values,
            scale=1,
            distribution=AftDistribution.NORMAL,
        )
