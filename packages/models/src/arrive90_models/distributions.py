"""Audited AFT CDF formulas, grids, and quantile inversion."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum


class AftDistribution(StrEnum):
    NORMAL = "normal"
    LOGISTIC = "logistic"
    EXTREME = "extreme"


def _standard_cdf(value: float, distribution: AftDistribution) -> float:
    if distribution is AftDistribution.NORMAL:
        return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))
    if distribution is AftDistribution.LOGISTIC:
        if value >= 0:
            return 1.0 / (1.0 + math.exp(-value))
        exponent = math.exp(value)
        return exponent / (1.0 + exponent)
    exponent = math.exp(min(value, math.log(float.fromhex("0x1.fffffffffffffp+1023"))))
    return -math.expm1(-exponent)


def aft_cdf(
    duration_seconds: float,
    *,
    raw_margin: float,
    scale: float,
    distribution: AftDistribution,
) -> float:
    """Evaluate the XGBoost AFT latent arrival CDF from its raw margin."""

    if scale <= 0:
        raise ValueError("AFT distribution scale must be positive")
    if duration_seconds <= 0:
        return 0.0
    z_value = (math.log(duration_seconds) - raw_margin) / scale
    return min(1.0, max(0.0, _standard_cdf(z_value, distribution)))


@dataclass(frozen=True)
class CdfGrid:
    thresholds_seconds: tuple[int, ...]
    probabilities: tuple[float, ...]
    maximum_rounding_correction: float


def evaluate_cdf_grid(
    thresholds_seconds: tuple[int, ...],
    *,
    raw_margin: float,
    scale: float,
    distribution: AftDistribution,
    reversal_tolerance: float = 1e-12,
) -> CdfGrid:
    if not thresholds_seconds or tuple(sorted(set(thresholds_seconds))) != thresholds_seconds:
        raise ValueError("CDF thresholds must be unique and increasing")
    raw = tuple(
        aft_cdf(
            threshold,
            raw_margin=raw_margin,
            scale=scale,
            distribution=distribution,
        )
        for threshold in thresholds_seconds
    )
    corrected: list[float] = []
    maximum_correction = 0.0
    prior = 0.0
    for probability in raw:
        if probability < prior:
            correction = prior - probability
            if correction > reversal_tolerance:
                raise ValueError("CDF has a non-rounding monotonicity violation")
            maximum_correction = max(maximum_correction, correction)
            probability = prior
        corrected.append(probability)
        prior = probability
    return CdfGrid(thresholds_seconds, tuple(corrected), maximum_correction)


@dataclass(frozen=True)
class QuantileResult:
    probability: float
    lower_seconds: int | None
    upper_seconds: int | None
    resolved_within_horizon: bool


def invert_aft_cdf(
    probability: float,
    *,
    raw_margin: float,
    scale: float,
    distribution: AftDistribution,
    observation_horizon_seconds: int = 12_600,
) -> QuantileResult:
    """Find neighboring one-second timestamps bracketing one CDF probability."""

    if not 0 < probability < 1:
        raise ValueError("quantile probability must be strictly inside zero and one")
    if observation_horizon_seconds <= 0:
        raise ValueError("observation horizon must be positive")
    if (
        aft_cdf(
            observation_horizon_seconds,
            raw_margin=raw_margin,
            scale=scale,
            distribution=distribution,
        )
        < probability
    ):
        return QuantileResult(probability, None, None, False)
    low = 0
    high = observation_horizon_seconds
    while high - low > 1:
        middle = (low + high) // 2
        if (
            aft_cdf(
                middle,
                raw_margin=raw_margin,
                scale=scale,
                distribution=distribution,
            )
            >= probability
        ):
            high = middle
        else:
            low = middle
    return QuantileResult(probability, low, high, True)
