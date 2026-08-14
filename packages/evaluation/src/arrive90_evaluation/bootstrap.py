"""Deterministic paired complete-service-day block bootstrap."""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from arrive90_evaluation.metrics import (
    PolicyPairRow,
    PredictionRow,
    QuantileRow,
    calibration_summary,
    policy_pair_summary,
    quantile_summary,
)


class ServiceDayRow(Protocol):
    @property
    def service_day(self) -> str: ...


@dataclass(frozen=True)
class BootstrapInterval:
    estimate: float
    lower_95: float
    upper_95: float
    replicates: int
    service_day_blocks: int
    seed: int


@dataclass(frozen=True)
class PolicyBoundsBootstrap:
    difference_lower: BootstrapInterval
    difference_upper: BootstrapInterval


def paired_block_bootstrap[RowT: ServiceDayRow](
    rows: tuple[RowT, ...],
    statistic: Callable[[tuple[RowT, ...]], float],
    *,
    replicates: int = 2_000,
    seed: int,
) -> BootstrapInterval:
    if replicates < 2_000:
        raise ValueError("final comparisons require at least 2000 bootstrap replicates")
    grouped: dict[str, list[RowT]] = {}
    for row in rows:
        grouped.setdefault(row.service_day, []).append(row)
    days = tuple(sorted(grouped, key=str.encode))
    if len(days) < 2:
        raise ValueError("block bootstrap requires at least two complete service days")
    generator = random.Random(seed)  # noqa: S311 - deterministic statistical resampling
    samples: list[float] = []
    for _index in range(replicates):
        replicate_rows: list[RowT] = []
        for _block in days:
            selected = days[generator.randrange(len(days))]
            replicate_rows.extend(grouped[selected])
        samples.append(statistic(tuple(replicate_rows)))
    samples.sort()
    lower_index = int((len(samples) - 1) * 0.025)
    upper_index = int((len(samples) - 1) * 0.975)
    return BootstrapInterval(
        statistic(rows),
        samples[lower_index],
        samples[upper_index],
        replicates,
        len(days),
        seed,
    )


def bootstrap_policy_bounds(
    rows: tuple[PolicyPairRow, ...],
    *,
    replicates: int = 2_000,
    seed: int,
) -> PolicyBoundsBootstrap:
    return PolicyBoundsBootstrap(
        paired_block_bootstrap(
            rows,
            lambda sample: policy_pair_summary(sample).difference_lower,
            replicates=replicates,
            seed=seed,
        ),
        paired_block_bootstrap(
            rows,
            lambda sample: policy_pair_summary(sample).difference_upper,
            replicates=replicates,
            seed=seed,
        ),
    )


def bootstrap_calibration_bound(
    rows: tuple[PredictionRow, ...],
    *,
    replicates: int = 2_000,
    seed: int,
) -> BootstrapInterval:
    return paired_block_bootstrap(
        rows,
        lambda sample: calibration_summary(sample).worst_case_absolute_gap,
        replicates=replicates,
        seed=seed,
    )


def bootstrap_quantile_coverage_gap(
    rows: tuple[QuantileRow, ...],
    *,
    replicates: int = 2_000,
    seed: int,
) -> BootstrapInterval:
    return paired_block_bootstrap(
        rows,
        lambda sample: quantile_summary(sample).worst_case_coverage_gap,
        replicates=replicates,
        seed=seed,
    )
