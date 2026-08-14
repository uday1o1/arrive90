"""Weighted resolved-only predictive diagnostics kept separate from decision bounds."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class ResolvedProbabilityRow:
    probability: float
    success: bool
    weight: float

    def __post_init__(self) -> None:
        if not 0 <= self.probability <= 1 or self.weight <= 0:
            raise ValueError("resolved probability row is invalid")


@dataclass(frozen=True)
class ProbabilityMetrics:
    brier_score: float
    log_loss: float
    expected_calibration_error: float
    maximum_calibration_error: float
    bins: int


def probability_metrics(
    rows: tuple[ResolvedProbabilityRow, ...], *, bins: int = 10
) -> ProbabilityMetrics:
    if not rows or bins <= 1:
        raise ValueError("probability metrics require rows and at least two bins")
    total = sum(row.weight for row in rows)
    brier = sum(row.weight * (row.probability - float(row.success)) ** 2 for row in rows) / total
    log_terms = []
    for row in rows:
        if (row.success and row.probability == 0) or (not row.success and row.probability == 1):
            log_terms.append(math.inf)
        else:
            probability = row.probability if row.success else 1 - row.probability
            log_terms.append(-row.weight * math.log(probability))
    log_loss = math.inf if any(math.isinf(term) for term in log_terms) else sum(log_terms) / total
    groups: list[list[ResolvedProbabilityRow]] = [[] for _index in range(bins)]
    for row in rows:
        index = min(bins - 1, int(row.probability * bins))
        groups[index].append(row)
    expected_gap = 0.0
    maximum_gap = 0.0
    for group in groups:
        if not group:
            continue
        mass = sum(row.weight for row in group)
        prediction = sum(row.weight * row.probability for row in group) / mass
        outcome = sum(row.weight * float(row.success) for row in group) / mass
        gap = abs(prediction - outcome)
        expected_gap += mass / total * gap
        maximum_gap = max(maximum_gap, gap)
    return ProbabilityMetrics(brier, log_loss, expected_gap, maximum_gap, bins)


@dataclass(frozen=True)
class PointPredictionRow:
    predicted: float
    observed: float
    weight: float

    def __post_init__(self) -> None:
        if self.weight <= 0:
            raise ValueError("point-prediction weight must be positive")


def weighted_mae(rows: tuple[PointPredictionRow, ...]) -> float:
    if not rows:
        raise ValueError("MAE rows require positive weight")
    total = sum(row.weight for row in rows)
    return sum(row.weight * abs(row.predicted - row.observed) for row in rows) / total
