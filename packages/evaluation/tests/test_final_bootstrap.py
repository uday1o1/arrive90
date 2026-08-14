from __future__ import annotations

import numpy as np
import pytest
from arrive90_evaluation.final_bootstrap import (
    FINAL_BOOTSTRAP_REPLICATES,
    bootstrap_weighted_quantile,
    bootstrap_weighted_ratio,
    bootstrap_weighted_ratio_difference,
    build_service_day_bootstrap_plan,
)


def test_exact_service_day_bootstrap_is_deterministic_and_paired() -> None:
    days = ("2024-11-01", "2024-11-02", "2024-11-03")
    first = build_service_day_bootstrap_plan(days, seed=90)
    second = build_service_day_bootstrap_plan(days, seed=90)
    assert first.manifest_sha256 == second.manifest_sha256
    assert np.array_equal(first.counts, second.counts)
    assert first.counts.shape == (FINAL_BOOTSTRAP_REPLICATES, 3)
    assert np.all(first.counts.sum(axis=1) == 3)

    day_indices = np.asarray([0, 0, 1, 1, 2, 2], dtype=np.int16)
    weights = np.ones(6, dtype=np.float64)
    values = np.asarray([1, 2, 3, 4, 5, 6], dtype=np.float64)
    ratio = bootstrap_weighted_ratio(values, weights, day_indices, first)
    assert ratio.estimate == 3.5
    assert ratio.replicates == 2_000
    assert ratio.service_day_blocks == 3
    assert ratio.lower_95 <= ratio.estimate <= ratio.upper_95
    difference = bootstrap_weighted_ratio_difference(
        values,
        values - 1,
        weights,
        day_indices,
        first,
    )
    assert difference.estimate == 1.0
    quantile = bootstrap_weighted_quantile(
        values,
        weights,
        day_indices,
        first,
        probability=0.5,
    )
    assert quantile.estimate == 3.0


def test_final_bootstrap_rejects_weakened_or_invalid_inputs() -> None:
    with pytest.raises(ValueError, match="exactly 2000"):
        build_service_day_bootstrap_plan(("a", "b"), seed=1, replicates=1_999)
    with pytest.raises(ValueError, match="unique"):
        build_service_day_bootstrap_plan(("b", "a"), seed=1)
    plan = build_service_day_bootstrap_plan(("a", "b"), seed=1)
    with pytest.raises(ValueError, match="weighted-ratio"):
        bootstrap_weighted_ratio(
            np.asarray([], dtype=np.float64),
            np.asarray([], dtype=np.float64),
            np.asarray([], dtype=np.int16),
            plan,
        )
    with pytest.raises(ValueError, match="weighted-quantile"):
        bootstrap_weighted_quantile(
            np.asarray([1.0]),
            np.asarray([1.0]),
            np.asarray([0], dtype=np.int16),
            plan,
            probability=1.0,
        )
