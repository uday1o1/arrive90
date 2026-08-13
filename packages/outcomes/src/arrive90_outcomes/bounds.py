"""Automatic complete-population bounds for unresolved binary outcomes."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class WeightedBinaryOutcome:
    success: bool | None
    weight: float


@dataclass(frozen=True)
class BinaryBounds:
    lower: float
    upper: float
    resolved_weight: float
    total_weight: float


def binary_success_bounds(outcomes: Iterable[WeightedBinaryOutcome]) -> BinaryBounds:
    rows = tuple(outcomes)
    total = sum(row.weight for row in rows)
    if total <= 0 or any(row.weight <= 0 for row in rows):
        raise ValueError("bound weights must be positive")
    successes = sum(row.weight for row in rows if row.success is True)
    unresolved = sum(row.weight for row in rows if row.success is None)
    resolved = total - unresolved
    return BinaryBounds(successes / total, (successes + unresolved) / total, resolved, total)


@dataclass(frozen=True)
class WeightedPolicyPair:
    arrive90_success: bool | None
    comparator_success: bool | None
    weight: float


def paired_difference_bounds(pairs: Iterable[WeightedPolicyPair]) -> BinaryBounds:
    rows = tuple(pairs)
    total = sum(row.weight for row in rows)
    if total <= 0 or any(row.weight <= 0 for row in rows):
        raise ValueError("bound weights must be positive")
    lower_sum = 0.0
    upper_sum = 0.0
    resolved_weight = 0.0
    for row in rows:
        arrive90 = row.arrive90_success
        comparator = row.comparator_success
        if arrive90 is not None and comparator is not None:
            contribution = float(arrive90) - float(comparator)
            lower_sum += row.weight * contribution
            upper_sum += row.weight * contribution
            resolved_weight += row.weight
        elif comparator is not None:
            lower_sum += row.weight * -float(comparator)
            upper_sum += row.weight * (1.0 - float(comparator))
        elif arrive90 is not None:
            lower_sum += row.weight * (float(arrive90) - 1.0)
            upper_sum += row.weight * float(arrive90)
        else:
            lower_sum -= row.weight
            upper_sum += row.weight
    return BinaryBounds(lower_sum / total, upper_sum / total, resolved_weight, total)
