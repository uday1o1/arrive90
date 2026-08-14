"""Weighted predictive and selected-policy metrics with unresolved-outcome bounds."""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

from arrive90_outcomes.bounds import WeightedPolicyPair, paired_difference_bounds

DEADLINE_BANDS = (
    (Decimal("0.00"), Decimal("0.10"), False),
    (Decimal("0.10"), Decimal("0.20"), False),
    (Decimal("0.20"), Decimal("0.30"), False),
    (Decimal("0.30"), Decimal("0.40"), False),
    (Decimal("0.40"), Decimal("0.50"), False),
    (Decimal("0.50"), Decimal("0.60"), False),
    (Decimal("0.60"), Decimal("0.70"), False),
    (Decimal("0.70"), Decimal("0.80"), False),
    (Decimal("0.80"), Decimal("0.90"), False),
    (Decimal("0.90"), Decimal("0.95"), False),
    (Decimal("0.95"), Decimal("1.00"), True),
)


@dataclass(frozen=True)
class PredictionRow:
    decision_id: str
    base_query_id: str
    service_day: str
    weight: float
    rounded_probability: Decimal
    unrounded_probability: float
    success: bool | None

    def __post_init__(self) -> None:
        if self.weight <= 0:
            raise ValueError("prediction weight must be positive")
        if not Decimal("0") <= self.rounded_probability <= Decimal("1"):
            raise ValueError("rounded prediction must be inside zero and one")
        if not 0 <= self.unrounded_probability <= 1:
            raise ValueError("unrounded prediction must be inside zero and one")


@dataclass(frozen=True)
class CalibrationSummary:
    predicted_mean: float
    success_lower: float
    success_upper: float
    worst_case_absolute_gap: float
    decision_count: int
    resolved_count: int
    distinct_base_queries: int
    service_day_blocks: int
    weighted_mass: float
    resolved_weighted_mass: float
    cluster_adjusted_effective_sample_size: float


def deadline_band_id(probability: Decimal) -> str:
    for lower, upper, right_closed in DEADLINE_BANDS:
        if lower <= probability < upper or (right_closed and probability == upper):
            return f"[{lower:.2f},{upper:.2f}{']' if right_closed else ')'}"
    raise ValueError("probability does not belong to a frozen deadline band")


def calibration_summary(rows: tuple[PredictionRow, ...]) -> CalibrationSummary:
    if not rows:
        raise ValueError("calibration population cannot be empty")
    total = sum(row.weight for row in rows)
    predicted = sum(row.weight * row.unrounded_probability for row in rows) / total
    resolved_success = sum(row.weight for row in rows if row.success is True)
    unresolved = sum(row.weight for row in rows if row.success is None)
    resolved_mass = total - unresolved
    lower = resolved_success / total
    upper = (resolved_success + unresolved) / total
    day_masses: dict[str, float] = {}
    for row in rows:
        day_masses[row.service_day] = day_masses.get(row.service_day, 0.0) + row.weight
    effective = total * total / sum(mass * mass for mass in day_masses.values())
    return CalibrationSummary(
        predicted,
        lower,
        upper,
        max(abs(predicted - lower), abs(predicted - upper)),
        len(rows),
        sum(row.success is not None for row in rows),
        len({row.base_query_id for row in rows}),
        len(day_masses),
        total,
        resolved_mass,
        effective,
    )


def calibration_by_deadline_band(
    rows: tuple[PredictionRow, ...],
) -> tuple[tuple[str, CalibrationSummary], ...]:
    grouped: dict[str, list[PredictionRow]] = {}
    for row in rows:
        grouped.setdefault(deadline_band_id(row.rounded_probability), []).append(row)
    return tuple(
        (band_id, calibration_summary(tuple(grouped[band_id])))
        for band_id in sorted(grouped, key=str.encode)
    )


@dataclass(frozen=True)
class TransferPredictionRow:
    transfer_row_id: str
    base_query_id: str
    service_day: str
    station_id: str
    weight: float
    rounded_probability: Decimal
    unrounded_probability: float
    success: bool | None

    def __post_init__(self) -> None:
        if self.weight <= 0:
            raise ValueError("transfer prediction weight must be positive")
        if not Decimal("0") <= self.rounded_probability <= Decimal("1"):
            raise ValueError("rounded transfer prediction must be inside zero and one")
        if not 0 <= self.unrounded_probability <= 1:
            raise ValueError("unrounded transfer prediction must be inside zero and one")


@dataclass(frozen=True)
class TransferStationSummary:
    expected_calibration_bound: float
    decision_count: int
    resolved_count: int
    distinct_base_queries: int
    service_day_blocks: int
    weighted_mass: float


def transfer_decile_id(probability: Decimal) -> str:
    index = min(9, int(probability * 10))
    if probability < 0 or probability > 1:
        raise ValueError("probability does not belong to a frozen transfer decile")
    lower = Decimal(index) / 10
    upper = Decimal(index + 1) / 10
    return f"[{lower:.1f},{upper:.1f}{']' if index == 9 else ')'}"


def _as_prediction(row: TransferPredictionRow) -> PredictionRow:
    return PredictionRow(
        row.transfer_row_id,
        row.base_query_id,
        row.service_day,
        row.weight,
        row.rounded_probability,
        row.unrounded_probability,
        row.success,
    )


def transfer_calibration_by_decile(
    rows: tuple[TransferPredictionRow, ...],
) -> tuple[tuple[str, CalibrationSummary], ...]:
    grouped: dict[str, list[TransferPredictionRow]] = {}
    for row in rows:
        grouped.setdefault(transfer_decile_id(row.rounded_probability), []).append(row)
    return tuple(
        (
            decile_id,
            calibration_summary(tuple(_as_prediction(row) for row in grouped[decile_id])),
        )
        for decile_id in sorted(grouped, key=str.encode)
    )


def transfer_station_summary(
    rows: tuple[TransferPredictionRow, ...],
) -> TransferStationSummary:
    if not rows or len({row.station_id for row in rows}) != 1:
        raise ValueError("station calibration requires one nonempty station population")
    deciles = transfer_calibration_by_decile(rows)
    total = sum(row.weight for row in rows)
    expected = (
        sum(summary.weighted_mass * summary.worst_case_absolute_gap for _decile, summary in deciles)
        / total
    )
    return TransferStationSummary(
        expected,
        len(rows),
        sum(row.success is not None for row in rows),
        len({row.base_query_id for row in rows}),
        len({row.service_day for row in rows}),
        total,
    )


@dataclass(frozen=True)
class PolicyPairRow:
    variant_id: str
    base_query_id: str
    service_day: str
    weight: float
    arrive90_success: bool | None
    comparator_success: bool | None
    added_planned_time_seconds: int | None
    slice_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.weight <= 0:
            raise ValueError("policy-pair weight must be positive")


@dataclass(frozen=True)
class PolicyPairSummary:
    difference_lower: float
    difference_upper: float
    paired_resolved_estimate: float | None
    pair_resolution_rate: float
    total_weight: float
    resolved_weight: float
    mean_added_planned_time_seconds: float | None
    p95_added_planned_time_seconds: int | None
    maximum_added_planned_time_seconds: int | None


@dataclass(frozen=True)
class OutcomeBounds:
    success_lower: float
    success_upper: float
    resolved_rate: float


def _weighted_quantile(values: tuple[tuple[int, float], ...], quantile: float) -> int:
    ordered = sorted(values)
    target = sum(weight for _value, weight in ordered) * quantile
    cumulative = 0.0
    for value, weight in ordered:
        cumulative += weight
        if cumulative >= target:
            return value
    return ordered[-1][0]


def _resolved_difference(row: PolicyPairRow) -> float:
    if row.arrive90_success is None or row.comparator_success is None:
        raise ValueError("paired-resolved contribution is unresolved")
    return float(row.arrive90_success) - float(row.comparator_success)


def policy_pair_summary(rows: tuple[PolicyPairRow, ...]) -> PolicyPairSummary:
    if not rows:
        raise ValueError("policy comparison cannot be empty")
    bounds = paired_difference_bounds(
        WeightedPolicyPair(row.arrive90_success, row.comparator_success, row.weight) for row in rows
    )
    resolved = tuple(
        row
        for row in rows
        if row.arrive90_success is not None and row.comparator_success is not None
    )
    resolved_estimate = None
    if resolved:
        resolved_weight = sum(row.weight for row in resolved)
        resolved_estimate = (
            sum(row.weight * _resolved_difference(row) for row in resolved) / resolved_weight
        )
    added = tuple(
        (row.added_planned_time_seconds, row.weight)
        for row in rows
        if row.added_planned_time_seconds is not None
    )
    mean_added = None
    p95_added = None
    maximum_added = None
    if added:
        added_mass = sum(weight for _value, weight in added)
        mean_added = sum(value * weight for value, weight in added) / added_mass
        p95_added = _weighted_quantile(added, 0.95)
        maximum_added = max(value for value, _weight in added)
    return PolicyPairSummary(
        bounds.lower,
        bounds.upper,
        resolved_estimate,
        bounds.resolved_weight / bounds.total_weight,
        bounds.total_weight,
        bounds.resolved_weight,
        mean_added,
        p95_added,
        maximum_added,
    )


def policy_outcome_bounds(rows: tuple[PolicyPairRow, ...], *, policy: str) -> OutcomeBounds:
    """Return complete-population success bounds for one side of a policy pair."""

    if not rows or policy not in {"arrive90", "comparator"}:
        raise ValueError("outcome bounds require rows and a named policy")
    values = (
        tuple(row.arrive90_success for row in rows)
        if policy == "arrive90"
        else tuple(row.comparator_success for row in rows)
    )
    total = sum(row.weight for row in rows)
    success = sum(row.weight for row, value in zip(rows, values, strict=True) if value is True)
    unresolved = sum(row.weight for row, value in zip(rows, values, strict=True) if value is None)
    return OutcomeBounds(success / total, (success + unresolved) / total, 1 - unresolved / total)


def resolution_rates_by_slice(rows: tuple[PolicyPairRow, ...]) -> dict[str, float]:
    if not rows:
        raise ValueError("resolution population cannot be empty")
    grouped: dict[str, list[PolicyPairRow]] = {"OVERALL": list(rows)}
    for row in rows:
        for slice_id in row.slice_ids:
            grouped.setdefault(slice_id, []).append(row)
    rates: dict[str, float] = {}
    for slice_id, members in grouped.items():
        total = sum(row.weight for row in members)
        resolved = sum(
            row.weight
            for row in members
            if row.arrive90_success is not None and row.comparator_success is not None
        )
        rates[slice_id] = resolved / total
    return rates


@dataclass(frozen=True)
class QuantileRow:
    service_day: str
    weight: float
    quantile_level: float
    predicted_seconds: float
    arrival_lower_seconds: float | None
    arrival_upper_seconds: float | None

    def __post_init__(self) -> None:
        if self.weight <= 0 or not 0 < self.quantile_level < 1:
            raise ValueError("quantile row has invalid weight or level")


@dataclass(frozen=True)
class QuantileSummary:
    coverage_lower: float
    coverage_upper: float
    worst_case_coverage_gap: float
    pinball_loss_lower: float | None
    pinball_loss_upper: float | None
    finite_weighted_mass: float
    excluded_censored_weighted_mass: float


def _pinball(value: float, predicted: float, quantile: float) -> float:
    residual = value - predicted
    return quantile * residual if residual >= 0 else (quantile - 1.0) * residual


def quantile_summary(rows: tuple[QuantileRow, ...]) -> QuantileSummary:
    if not rows:
        raise ValueError("quantile population cannot be empty")
    levels = {row.quantile_level for row in rows}
    if len(levels) != 1:
        raise ValueError("one quantile summary cannot mix levels")
    level = next(iter(levels))
    total = sum(row.weight for row in rows)
    lower_coverage = 0.0
    upper_coverage = 0.0
    loss_lower = 0.0
    loss_upper = 0.0
    finite_mass = 0.0
    for row in rows:
        lower = row.arrival_lower_seconds
        upper = row.arrival_upper_seconds
        if upper is not None and upper <= row.predicted_seconds:
            lower_coverage += row.weight
        if lower is None or lower <= row.predicted_seconds:
            upper_coverage += row.weight
        if (
            lower is not None
            and upper is not None
            and math.isfinite(lower)
            and math.isfinite(upper)
        ):
            endpoint_losses = (
                _pinball(lower, row.predicted_seconds, level),
                _pinball(upper, row.predicted_seconds, level),
            )
            minimum = 0.0 if lower <= row.predicted_seconds <= upper else min(endpoint_losses)
            loss_lower += row.weight * minimum
            loss_upper += row.weight * max(endpoint_losses)
            finite_mass += row.weight
    coverage_lower = lower_coverage / total
    coverage_upper = upper_coverage / total
    return QuantileSummary(
        coverage_lower,
        coverage_upper,
        max(abs(level - coverage_lower), abs(level - coverage_upper)),
        loss_lower / finite_mass if finite_mass else None,
        loss_upper / finite_mass if finite_mass else None,
        finite_mass,
        total - finite_mass,
    )
