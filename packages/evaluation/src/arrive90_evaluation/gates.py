"""Frozen output-support gates for final-test evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from arrive90_evaluation.freeze import FrozenCellResult, frozen_policy_passes


class CellKind(StrEnum):
    DEADLINE_BAND = "DEADLINE_BAND"
    DEADLINE_SLICE = "DEADLINE_SLICE"
    PROSPECTIVE_095 = "PROSPECTIVE_095"
    TRANSFER_DECILE = "TRANSFER_DECILE"
    TRANSFER_STATION = "TRANSFER_STATION"
    SELECTED_TRIGGER_DECILE = "SELECTED_TRIGGER_DECILE"
    QUANTILE_LEVEL = "QUANTILE_LEVEL"


@dataclass(frozen=True)
class CellEvidence:
    cell_id: str
    kind: CellKind
    decision_count: int
    base_query_count: int
    service_day_count: int
    uncertainty_upper: float

    def __post_init__(self) -> None:
        if min(self.decision_count, self.base_query_count, self.service_day_count) < 0:
            raise ValueError("support counts cannot be negative")
        if not 0 <= self.uncertainty_upper <= 1:
            raise ValueError("uncertainty upper bound must be inside zero and one")


@dataclass(frozen=True)
class CellGateResult:
    cell_id: str
    eligible: bool
    reasons: tuple[str, ...]


_THRESHOLDS = {
    CellKind.DEADLINE_BAND: (500, 250, 50, 0.05),
    CellKind.DEADLINE_SLICE: (250, 125, 30, 0.08),
    CellKind.PROSPECTIVE_095: (800, 400, 56, 0.03),
    CellKind.TRANSFER_DECILE: (500, 250, 40, 0.08),
    CellKind.TRANSFER_STATION: (800, 400, 50, 0.08),
    CellKind.SELECTED_TRIGGER_DECILE: (300, 150, 30, 0.08),
    CellKind.QUANTILE_LEVEL: (1_000, 500, 50, 0.08),
}


def evaluate_cell_gate(evidence: CellEvidence) -> CellGateResult:
    decisions, queries, days, uncertainty = _THRESHOLDS[evidence.kind]
    reasons: list[str] = []
    if evidence.decision_count < decisions:
        reasons.append("DECISION_COUNT_BELOW_MINIMUM")
    if evidence.base_query_count < queries:
        reasons.append("BASE_QUERY_COUNT_BELOW_MINIMUM")
    if evidence.service_day_count < days:
        reasons.append("SERVICE_DAY_COUNT_BELOW_MINIMUM")
    if evidence.uncertainty_upper > uncertainty:
        reasons.append("UNCERTAINTY_UPPER_EXCEEDS_MAXIMUM")
    return CellGateResult(evidence.cell_id, not reasons, tuple(reasons))


@dataclass(frozen=True)
class PerformanceEvidence:
    scoring_p95_ms: float
    recovery_p95_ms: float
    cached_search_p95_ms: float

    def __post_init__(self) -> None:
        if min(self.scoring_p95_ms, self.recovery_p95_ms, self.cached_search_p95_ms) < 0:
            raise ValueError("performance measurements cannot be negative")


@dataclass(frozen=True)
class PrimaryGateEvidence:
    empirical_primary_evidence: bool
    primary_difference_lower_ci: float
    pair_resolution_rate: float
    slice_pair_resolution_rates: tuple[tuple[str, float], ...]
    mean_added_planned_time_seconds: float
    maximum_added_planned_time_seconds: int
    added_time_population_available: bool
    performance: PerformanceEvidence
    frozen_cells: tuple[FrozenCellResult, ...]

    def __post_init__(self) -> None:
        rates = (
            self.pair_resolution_rate,
            *(rate for _name, rate in self.slice_pair_resolution_rates),
        )
        if any(not 0 <= rate <= 1 for rate in rates):
            raise ValueError("resolution rates must be inside zero and one")
        names = tuple(name for name, _rate in self.slice_pair_resolution_rates)
        if len(set(names)) != len(names):
            raise ValueError("slice resolution identifiers must be unique")
        if self.mean_added_planned_time_seconds < 0 or self.maximum_added_planned_time_seconds < 0:
            raise ValueError("added planned time cannot be negative")


@dataclass(frozen=True)
class PrimaryGateResult:
    passed: bool
    failing_checks: tuple[str, ...]


def evaluate_primary_gate(evidence: PrimaryGateEvidence) -> PrimaryGateResult:
    """Apply the frozen confirmatory gate without changing output eligibility."""

    failures: list[str] = []
    if not evidence.empirical_primary_evidence:
        failures.append("EMPIRICAL_PRIMARY_EVIDENCE_UNAVAILABLE")
    if evidence.primary_difference_lower_ci <= 0:
        failures.append("PRIMARY_WORST_CASE_BOUND_LOWER_CI_NOT_ABOVE_ZERO")
    if evidence.pair_resolution_rate < 0.90:
        failures.append("PRIMARY_PAIR_RESOLUTION_RATE_BELOW_0_90")
    for slice_id, rate in evidence.slice_pair_resolution_rates:
        if rate < 0.80:
            failures.append(f"SLICE_PAIR_RESOLUTION_RATE_BELOW_0_80:{slice_id}")
    if evidence.mean_added_planned_time_seconds > 600:
        failures.append("MEAN_ADDED_PLANNED_TIME_EXCEEDS_600_SECONDS")
    if evidence.maximum_added_planned_time_seconds > 1_200:
        failures.append("MAXIMUM_ADDED_PLANNED_TIME_EXCEEDS_1200_SECONDS")
    if not evidence.added_time_population_available:
        failures.append("ADDED_TIME_POPULATION_UNAVAILABLE")
    if evidence.performance.scoring_p95_ms >= 100:
        failures.append("SCORING_P95_NOT_BELOW_100_MS")
    if evidence.performance.recovery_p95_ms >= 100:
        failures.append("RECOVERY_P95_NOT_BELOW_100_MS")
    if evidence.performance.cached_search_p95_ms >= 1_000:
        failures.append("CACHED_SEARCH_P95_NOT_BELOW_1000_MS")
    if not frozen_policy_passes(evidence.frozen_cells):
        failures.append("PRETEST_ELIGIBLE_OUTPUT_SUPPORT_CELL_FAILED")
    return PrimaryGateResult(not failures, tuple(failures))
