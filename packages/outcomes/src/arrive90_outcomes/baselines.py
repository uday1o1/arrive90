"""Frozen schedule, empirical, threshold, point, and official-estimate baselines."""

from __future__ import annotations

import math
from dataclasses import dataclass

from arrive90_data_contracts.candidates import CandidateItinerary
from arrive90_routing.candidates import candidate_order


@dataclass(frozen=True)
class BaselineContext:
    candidate_manifest_hash: str
    temporal_view_hash: str
    query_manifest_hash: str
    outcome_resolver_hash: str
    decision_context_hash: str

    def require_same_evidence(self, other: BaselineContext) -> None:
        if self != other:
            raise ValueError("baseline contexts do not share identical frozen evidence")


def static_schedule_probability(
    *, scheduled_arrival_seconds: float, deadline_seconds: float
) -> float:
    return float(scheduled_arrival_seconds <= deadline_seconds)


def official_prediction_probability(
    *, predicted_arrival_seconds: float | None, deadline_seconds: float
) -> float | None:
    if predicted_arrival_seconds is None:
        return None
    return float(predicted_arrival_seconds <= deadline_seconds)


def fastest_candidate(candidates: tuple[CandidateItinerary, ...]) -> CandidateItinerary:
    if not candidates:
        raise ValueError("fastest-candidate baseline requires at least one candidate")
    return min(candidates, key=candidate_order)


@dataclass(frozen=True)
class DelayObservation:
    group_key: tuple[str, ...]
    delay_seconds: float
    weight: float = 1.0


class RollingMedianDelay:
    def __init__(self, medians: dict[tuple[str, ...], float]) -> None:
        self.medians = medians

    @classmethod
    def fit(cls, observations: tuple[DelayObservation, ...]) -> RollingMedianDelay:
        grouped: dict[tuple[str, ...], list[tuple[float, float]]] = {}
        for observation in observations:
            if observation.weight <= 0:
                raise ValueError("baseline observation weights must be positive")
            grouped.setdefault(observation.group_key, []).append(
                (observation.delay_seconds, observation.weight)
            )
        medians: dict[tuple[str, ...], float] = {}
        for key, values in grouped.items():
            ordered = sorted(values)
            midpoint = sum(weight for _, weight in ordered) / 2
            cumulative = 0.0
            for value, weight in ordered:
                cumulative += weight
                if cumulative >= midpoint:
                    medians[key] = value
                    break
        return cls(medians)

    def predict(self, group_key: tuple[str, ...]) -> float | None:
        return self.medians.get(group_key)


class EmpiricalTimeDistribution:
    def __init__(self, sorted_durations: dict[tuple[str, ...], tuple[float, ...]]) -> None:
        self.sorted_durations = sorted_durations

    @classmethod
    def fit(
        cls, observations: tuple[tuple[tuple[str, ...], float], ...]
    ) -> EmpiricalTimeDistribution:
        grouped: dict[tuple[str, ...], list[float]] = {}
        for key, duration in observations:
            if duration <= 0:
                raise ValueError("empirical duration must be positive")
            grouped.setdefault(key, []).append(duration)
        return cls({key: tuple(sorted(values)) for key, values in grouped.items()})

    def cdf(self, group_key: tuple[str, ...], deadline_seconds: float) -> float | None:
        values = self.sorted_durations.get(group_key)
        if not values:
            return None
        return sum(value <= deadline_seconds for value in values) / len(values)

    def quantile(self, group_key: tuple[str, ...], probability: float) -> float | None:
        if not 0 <= probability <= 1:
            raise ValueError("quantile probability must be inside zero and one")
        values = self.sorted_durations.get(group_key)
        if not values:
            return None
        index = max(0, math.ceil(probability * len(values)) - 1)
        return values[index]


@dataclass(frozen=True)
class ThresholdExample:
    deadline_slack_minutes: float
    success: bool
    weight: float


@dataclass(frozen=True)
class MonotonicLogisticDeadline:
    intercept: float
    nonnegative_slack_coefficient: float

    def probability(self, deadline_slack_minutes: float) -> float:
        margin = self.intercept + self.nonnegative_slack_coefficient * deadline_slack_minutes
        if margin >= 0:
            return 1.0 / (1.0 + math.exp(-margin))
        exponent = math.exp(margin)
        return exponent / (1.0 + exponent)


def fit_monotonic_logistic(
    examples: tuple[ThresholdExample, ...],
    *,
    l2: float = 0.01,
    learning_rate: float = 0.01,
    iterations: int = 2_000,
) -> MonotonicLogisticDeadline:
    if not examples or iterations <= 0 or learning_rate <= 0 or l2 < 0:
        raise ValueError("monotonic logistic fit configuration is invalid")
    if any(example.weight <= 0 for example in examples):
        raise ValueError("threshold example weights must be positive")
    total_weight = sum(example.weight for example in examples)
    intercept = 0.0
    coefficient = 0.0
    for _ in range(iterations):
        intercept_gradient = 0.0
        coefficient_gradient = 0.0
        model = MonotonicLogisticDeadline(intercept, coefficient)
        for example in examples:
            error = model.probability(example.deadline_slack_minutes) - float(example.success)
            intercept_gradient += example.weight * error
            coefficient_gradient += example.weight * error * example.deadline_slack_minutes
        intercept -= learning_rate * intercept_gradient / total_weight
        coefficient -= learning_rate * (coefficient_gradient / total_weight + l2 * coefficient)
        coefficient = max(0.0, coefficient)
    return MonotonicLogisticDeadline(intercept, coefficient)


class PointResidualDistribution:
    def __init__(self, residuals: tuple[float, ...]) -> None:
        if not residuals:
            raise ValueError("residual distribution cannot be empty")
        self.residuals = tuple(sorted(residuals))

    @classmethod
    def fit(cls, training_rows: tuple[tuple[float, float], ...]) -> PointResidualDistribution:
        return cls(tuple(observed - predicted for predicted, observed in training_rows))

    def cdf(self, point_prediction_seconds: float, deadline_seconds: float) -> float:
        threshold = deadline_seconds - point_prediction_seconds
        return sum(residual <= threshold for residual in self.residuals) / len(self.residuals)
