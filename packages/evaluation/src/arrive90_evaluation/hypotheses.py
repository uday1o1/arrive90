"""Frozen Holm familywise correction and Pareto reporting."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Hypothesis:
    hypothesis_id: str
    raw_p_value: float
    declared_order: int

    def __post_init__(self) -> None:
        if not 0 <= self.raw_p_value <= 1 or self.declared_order < 0:
            raise ValueError("hypothesis p-value or order is invalid")


@dataclass(frozen=True)
class HolmResult:
    hypothesis_id: str
    raw_p_value: float
    adjusted_p_value: float
    rejected: bool
    testing_rank: int


def holm_correction(
    hypotheses: tuple[Hypothesis, ...], *, alpha: float = 0.05
) -> tuple[HolmResult, ...]:
    if not hypotheses or not 0 < alpha < 1:
        raise ValueError("Holm correction requires hypotheses and a valid alpha")
    identifiers = tuple(item.hypothesis_id for item in hypotheses)
    orders = tuple(item.declared_order for item in hypotheses)
    if len(set(identifiers)) != len(identifiers) or len(set(orders)) != len(orders):
        raise ValueError("hypothesis identifiers and declared order must be unique")
    ordered = sorted(hypotheses, key=lambda item: (item.raw_p_value, item.declared_order))
    count = len(ordered)
    prior_adjusted = 0.0
    still_rejecting = True
    results: list[HolmResult] = []
    for index, item in enumerate(ordered):
        adjusted = min(1.0, max(prior_adjusted, (count - index) * item.raw_p_value))
        threshold = alpha / (count - index)
        rejected = still_rejecting and item.raw_p_value <= threshold
        if not rejected:
            still_rejecting = False
        results.append(
            HolmResult(item.hypothesis_id, item.raw_p_value, adjusted, rejected, index + 1)
        )
        prior_adjusted = adjusted
    by_id = {item.hypothesis_id: item for item in results}
    return tuple(by_id[identifier] for identifier in identifiers)


@dataclass(frozen=True)
class ParetoPoint:
    policy_id: str
    reliability_lower: float
    reliability_upper: float
    mean_added_time_seconds: float
    confirmatory: bool = False


def pareto_frontier(points: tuple[ParetoPoint, ...]) -> tuple[ParetoPoint, ...]:
    if not points or len({point.policy_id for point in points}) != len(points):
        raise ValueError("Pareto points must be nonempty and unique")
    frontier = []
    for candidate in points:
        dominated = any(
            other.policy_id != candidate.policy_id
            and other.reliability_lower >= candidate.reliability_lower
            and other.mean_added_time_seconds <= candidate.mean_added_time_seconds
            and (
                other.reliability_lower > candidate.reliability_lower
                or other.mean_added_time_seconds < candidate.mean_added_time_seconds
            )
            for other in points
        )
        if not dominated:
            frontier.append(candidate)
    return tuple(
        sorted(frontier, key=lambda item: (item.mean_added_time_seconds, item.policy_id.encode()))
    )
