from dataclasses import dataclass
from decimal import Decimal

import pytest
from arrive90_evaluation.bootstrap import (
    bootstrap_calibration_bound,
    bootstrap_policy_bounds,
    bootstrap_quantile_coverage_gap,
    paired_block_bootstrap,
)
from arrive90_evaluation.metrics import PolicyPairRow, PredictionRow, QuantileRow


@dataclass(frozen=True)
class Row:
    service_day: str
    value: float


def _mean(rows: tuple[Row, ...]) -> float:
    return sum(row.value for row in rows) / len(rows)


def test_complete_service_day_bootstrap_is_seeded_and_reproducible() -> None:
    rows = (
        Row("day-1", 0),
        Row("day-1", 2),
        Row("day-2", 8),
        Row("day-2", 10),
    )
    first = paired_block_bootstrap(rows, _mean, replicates=2_000, seed=17)
    second = paired_block_bootstrap(rows, _mean, replicates=2_000, seed=17)
    assert first == second
    assert first.estimate == 5
    assert first.lower_95 == 1
    assert first.upper_95 == 9
    assert first.service_day_blocks == 2


def test_bootstrap_rejects_row_level_or_underpowered_protocols() -> None:
    rows = (Row("day-1", 1), Row("day-1", 2))
    with pytest.raises(ValueError, match="2000"):
        paired_block_bootstrap(rows, _mean, replicates=1_999, seed=1)
    with pytest.raises(ValueError, match="two complete"):
        paired_block_bootstrap(rows, _mean, replicates=2_000, seed=1)


def test_policy_bound_bootstrap_recomputes_both_complete_population_bounds() -> None:
    rows = (
        PolicyPairRow("v1", "q1", "day-1", 1, True, False, 60),
        PolicyPairRow("v2", "q2", "day-2", 1, None, True, 60),
    )
    result = bootstrap_policy_bounds(rows, seed=9)
    assert result.difference_lower.estimate == 0
    assert result.difference_upper.estimate == 0.5
    assert result.difference_lower.replicates == 2_000


def test_calibration_and_quantile_bootstraps_recompute_complete_population_bounds() -> None:
    predictions = (
        PredictionRow("d1", "q1", "day-1", 1, Decimal("0.8"), 0.8, True),
        PredictionRow("d2", "q2", "day-2", 1, Decimal("0.8"), 0.8, None),
    )
    calibration = bootstrap_calibration_bound(predictions, seed=3)
    assert calibration.estimate == pytest.approx(0.3)
    assert calibration.replicates == 2_000
    quantiles = (
        QuantileRow("day-1", 1, 0.9, 10, 5, 8),
        QuantileRow("day-2", 1, 0.9, 10, None, None),
    )
    coverage = bootstrap_quantile_coverage_gap(quantiles, seed=4)
    assert coverage.estimate == pytest.approx(0.4)
    assert coverage.service_day_blocks == 2
