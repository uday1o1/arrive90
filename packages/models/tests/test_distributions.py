from __future__ import annotations

import math

import pytest
from arrive90_models.distributions import (
    AftDistribution,
    aft_cdf,
    evaluate_cdf_grid,
    invert_aft_cdf,
)


@pytest.mark.parametrize(
    ("distribution", "expected"),
    [
        (AftDistribution.NORMAL, 0.5),
        (AftDistribution.LOGISTIC, 0.5),
        (AftDistribution.EXTREME, 1 - math.exp(-1)),
    ],
)
def test_hand_computed_aft_cdf_at_zero_latent_residual(
    distribution: AftDistribution, expected: float
) -> None:
    assert aft_cdf(
        100, raw_margin=math.log(100), scale=1, distribution=distribution
    ) == pytest.approx(expected)
    assert aft_cdf(0, raw_margin=0, scale=1, distribution=distribution) == 0


def test_cdf_grid_is_bounded_monotonic_and_quantiles_bracket_probability() -> None:
    grid = evaluate_cdf_grid(
        (1, 60, 300, 600),
        raw_margin=math.log(300),
        scale=0.5,
        distribution=AftDistribution.LOGISTIC,
    )
    assert grid.probabilities == tuple(sorted(grid.probabilities))
    assert all(0 <= value <= 1 for value in grid.probabilities)
    quantile = invert_aft_cdf(
        0.5,
        raw_margin=math.log(300),
        scale=0.5,
        distribution=AftDistribution.LOGISTIC,
    )
    assert quantile.resolved_within_horizon
    assert quantile.lower_seconds == 299
    assert quantile.upper_seconds == 300
    assert (
        aft_cdf(
            quantile.lower_seconds,
            raw_margin=math.log(300),
            scale=0.5,
            distribution=AftDistribution.LOGISTIC,
        )
        < 0.5
    )
    assert (
        aft_cdf(
            quantile.upper_seconds,
            raw_margin=math.log(300),
            scale=0.5,
            distribution=AftDistribution.LOGISTIC,
        )
        >= 0.5
    )


def test_beyond_horizon_and_invalid_distribution_inputs_fail_closed() -> None:
    unresolved = invert_aft_cdf(
        0.99,
        raw_margin=math.log(10_000),
        scale=0.1,
        distribution=AftDistribution.NORMAL,
        observation_horizon_seconds=100,
    )
    assert not unresolved.resolved_within_horizon
    assert unresolved.lower_seconds is None
    with pytest.raises(ValueError, match="scale"):
        aft_cdf(1, raw_margin=0, scale=0, distribution=AftDistribution.NORMAL)
    with pytest.raises(ValueError, match="unique and increasing"):
        evaluate_cdf_grid((60, 60), raw_margin=0, scale=1, distribution=AftDistribution.NORMAL)
    with pytest.raises(ValueError, match="strictly inside"):
        invert_aft_cdf(1, raw_margin=0, scale=1, distribution=AftDistribution.NORMAL)
