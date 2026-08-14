"""Exactly 2,000 deterministic complete-service-day bootstrap replicates."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np

FINAL_BOOTSTRAP_REPLICATES = 2_000


@dataclass(frozen=True, slots=True)
class ServiceDayBootstrapPlan:
    """Frozen resampling counts for complete service-date blocks."""

    service_dates: tuple[str, ...]
    counts: np.ndarray
    seed: int
    manifest_sha256: str

    def __post_init__(self) -> None:
        expected_shape = (FINAL_BOOTSTRAP_REPLICATES, len(self.service_dates))
        if len(self.service_dates) < 2 or self.counts.shape != expected_shape:
            raise ValueError("service-day bootstrap plan has an invalid shape")
        if np.any(self.counts < 0) or np.any(
            np.sum(self.counts, axis=1) != len(self.service_dates)
        ):
            raise ValueError("every bootstrap replicate must contain complete day blocks")
        if len(self.manifest_sha256) != 64:
            raise ValueError("bootstrap manifest hash is invalid")


@dataclass(frozen=True, slots=True)
class BootstrapInterval:
    estimate: float
    lower_95: float
    upper_95: float
    replicates: int
    service_day_blocks: int
    seed: int
    quantile_method: str = "linear"


def build_service_day_bootstrap_plan(
    service_dates: tuple[str, ...],
    *,
    seed: int,
    replicates: int = FINAL_BOOTSTRAP_REPLICATES,
) -> ServiceDayBootstrapPlan:
    """Draw complete-day blocks with NumPy's pinned PCG64 generator."""

    if replicates != FINAL_BOOTSTRAP_REPLICATES:
        raise ValueError("final evaluation requires exactly 2000 bootstrap replicates")
    days = tuple(sorted(set(service_dates), key=str.encode))
    if days != service_dates or len(days) < 2:
        raise ValueError("bootstrap service dates must be unique and bytewise sorted")
    generator = np.random.Generator(np.random.PCG64(seed))
    draws = generator.integers(
        0,
        len(days),
        size=(replicates, len(days)),
        dtype=np.int16,
    )
    counts = np.zeros((replicates, len(days)), dtype=np.int16)
    for row_index, row in enumerate(draws):
        counts[row_index] = np.bincount(row, minlength=len(days)).astype(np.int16)
    digest = hashlib.sha256()
    digest.update("\n".join(days).encode())
    digest.update(b"\0")
    digest.update(str(seed).encode())
    digest.update(b"\0")
    digest.update(counts.tobytes(order="C"))
    return ServiceDayBootstrapPlan(days, counts, seed, digest.hexdigest())


def _percentile_interval(
    estimate: float,
    replicates: np.ndarray,
    plan: ServiceDayBootstrapPlan,
) -> BootstrapInterval:
    if replicates.shape != (FINAL_BOOTSTRAP_REPLICATES,) or np.any(~np.isfinite(replicates)):
        raise ValueError("bootstrap statistic produced invalid replicates")
    lower, upper = np.quantile(replicates, [0.025, 0.975], method="linear")
    return BootstrapInterval(
        estimate=float(estimate),
        lower_95=float(lower),
        upper_95=float(upper),
        replicates=FINAL_BOOTSTRAP_REPLICATES,
        service_day_blocks=len(plan.service_dates),
        seed=plan.seed,
    )


def bootstrap_weighted_ratio(
    numerators: np.ndarray,
    denominators: np.ndarray,
    day_indices: np.ndarray,
    plan: ServiceDayBootstrapPlan,
) -> BootstrapInterval:
    """Bootstrap an additive weighted ratio from complete service-day blocks."""

    row_count = len(numerators)
    if (
        row_count == 0
        or len(denominators) != row_count
        or len(day_indices) != row_count
        or np.any(~np.isfinite(numerators))
        or np.any(~np.isfinite(denominators))
        or np.any(denominators < 0)
        or np.any(day_indices < 0)
        or np.any(day_indices >= len(plan.service_dates))
    ):
        raise ValueError("bootstrap weighted-ratio rows are invalid")
    day_numerators = np.bincount(day_indices, weights=numerators, minlength=len(plan.service_dates))
    day_denominators = np.bincount(
        day_indices, weights=denominators, minlength=len(plan.service_dates)
    )
    estimate_denominator = float(np.sum(day_denominators))
    replicate_denominators = plan.counts @ day_denominators
    if estimate_denominator <= 0 or np.any(replicate_denominators <= 0):
        raise ValueError("bootstrap weighted ratio has an empty replicate")
    estimate = float(np.sum(day_numerators) / estimate_denominator)
    replicates = (plan.counts @ day_numerators) / replicate_denominators
    return _percentile_interval(estimate, replicates, plan)


def bootstrap_weighted_ratio_difference(
    first_numerators: np.ndarray,
    second_numerators: np.ndarray,
    denominators: np.ndarray,
    day_indices: np.ndarray,
    plan: ServiceDayBootstrapPlan,
) -> BootstrapInterval:
    """Bootstrap a paired difference on identical rows and day draws."""

    if len(first_numerators) != len(second_numerators):
        raise ValueError("paired bootstrap numerator arrays must align")
    difference = first_numerators - second_numerators
    return bootstrap_weighted_ratio(difference, denominators, day_indices, plan)


def bootstrap_weighted_quantile(
    values: np.ndarray,
    weights: np.ndarray,
    day_indices: np.ndarray,
    plan: ServiceDayBootstrapPlan,
    *,
    probability: float,
) -> BootstrapInterval:
    """Bootstrap one weighted quantile with complete-day multiplicities."""

    if (
        len(values) == 0
        or len(weights) != len(values)
        or len(day_indices) != len(values)
        or not 0 < probability < 1
        or np.any(~np.isfinite(values))
        or np.any(~np.isfinite(weights))
        or np.any(weights <= 0)
    ):
        raise ValueError("bootstrap weighted-quantile rows are invalid")
    order = np.lexsort((day_indices, values))
    ordered_values = values[order]
    ordered_weights = weights[order]
    ordered_days = day_indices[order]

    def weighted_quantile(block_counts: np.ndarray) -> float:
        effective = ordered_weights * block_counts[ordered_days]
        threshold = probability * float(np.sum(effective))
        if threshold <= 0:
            raise ValueError("bootstrap weighted quantile has an empty replicate")
        index = int(np.searchsorted(np.cumsum(effective), threshold, side="left"))
        return float(ordered_values[min(index, len(ordered_values) - 1)])

    estimate = weighted_quantile(np.ones(len(plan.service_dates), dtype=np.int16))
    replicates = np.asarray([weighted_quantile(counts) for counts in plan.counts], dtype=np.float64)
    return _percentile_interval(estimate, replicates, plan)
