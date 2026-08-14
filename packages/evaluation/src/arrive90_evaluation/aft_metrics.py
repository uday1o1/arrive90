"""Frozen pretest model-selection diagnostics for interval-censored AFT models."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from arrive90_models.distributions import AftDistribution
from scipy.special import expit, ndtr  # type: ignore[import-untyped]

MINIMUM_LIKELIHOOD = 1e-300


def _cdf(values: np.ndarray, distribution: AftDistribution) -> np.ndarray:
    if distribution is AftDistribution.NORMAL:
        return np.asarray(ndtr(values), dtype=np.float64)
    if distribution is AftDistribution.LOGISTIC:
        return np.asarray(expit(values), dtype=np.float64)
    exponent = np.exp(np.minimum(values, math.log(np.finfo(np.float64).max)))
    return np.asarray(-np.expm1(-exponent), dtype=np.float64)


def aft_cdf_matrix(
    raw_margins: np.ndarray,
    horizons_seconds: tuple[int, ...],
    *,
    scale: float,
    distribution: AftDistribution,
) -> np.ndarray:
    if scale <= 0 or not horizons_seconds:
        raise ValueError("CDF matrix configuration is invalid")
    horizons = np.asarray(horizons_seconds, dtype=np.float64)
    if np.any(horizons <= 0) or np.any(horizons[1:] <= horizons[:-1]):
        raise ValueError("CDF matrix horizons must be positive and increasing")
    latent = (np.log(horizons)[None, :] - raw_margins[:, None]) / scale
    values = _cdf(latent, distribution)
    return np.maximum.accumulate(np.clip(values, 0.0, 1.0), axis=1)


def weighted_interval_nll(
    raw_margins: np.ndarray,
    lower_bounds: np.ndarray,
    upper_bounds: np.ndarray,
    weights: np.ndarray,
    *,
    scale: float,
    distribution: AftDistribution,
) -> float:
    """Evaluate exact, interval, left, and right-censored log likelihood."""

    row_count = len(raw_margins)
    if (
        row_count == 0
        or any(len(values) != row_count for values in (lower_bounds, upper_bounds, weights))
        or scale <= 0
    ):
        raise ValueError("interval likelihood inputs are invalid")
    if (
        np.any(np.isnan(lower_bounds))
        or np.any(np.isnan(upper_bounds))
        or np.any(lower_bounds < 0)
        or np.any(upper_bounds <= 0)
        or np.any(upper_bounds < lower_bounds)
        or np.any(~np.isfinite(weights))
        or np.any(weights <= 0)
    ):
        raise ValueError("interval likelihood bounds or weights are invalid")
    log_likelihood = np.empty(row_count, dtype=np.float64)
    exact = np.isfinite(upper_bounds) & (lower_bounds == upper_bounds)
    right = np.isinf(upper_bounds)
    interval = ~(exact | right)
    if np.any(exact):
        durations = lower_bounds[exact]
        if np.any(durations <= 0):
            raise ValueError("exact event durations must be positive")
        latent = (np.log(durations) - raw_margins[exact]) / scale
        if distribution is AftDistribution.NORMAL:
            standard_log_density = -0.5 * latent**2 - 0.5 * math.log(2.0 * math.pi)
        elif distribution is AftDistribution.LOGISTIC:
            standard_log_density = -np.logaddexp(0.0, -latent) - np.logaddexp(0.0, latent)
        else:
            standard_log_density = latent - np.exp(
                np.minimum(latent, math.log(np.finfo(np.float64).max))
            )
        log_likelihood[exact] = standard_log_density - math.log(scale) - np.log(durations)
    if np.any(interval):
        upper_latent = (np.log(upper_bounds[interval]) - raw_margins[interval]) / scale
        upper_cdf = _cdf(upper_latent, distribution)
        lower_cdf = np.zeros_like(upper_cdf)
        positive_lower = lower_bounds[interval] > 0
        lower_cdf[positive_lower] = _cdf(
            (np.log(lower_bounds[interval][positive_lower]) - raw_margins[interval][positive_lower])
            / scale,
            distribution,
        )
        log_likelihood[interval] = np.log(np.maximum(upper_cdf - lower_cdf, MINIMUM_LIKELIHOOD))
    if np.any(right):
        if np.any(lower_bounds[right] <= 0):
            raise ValueError("right-censored lower bounds must be positive")
        lower_cdf = _cdf(
            (np.log(lower_bounds[right]) - raw_margins[right]) / scale,
            distribution,
        )
        log_likelihood[right] = np.log(np.maximum(1.0 - lower_cdf, MINIMUM_LIKELIHOOD))
    return float(-np.sum(weights * log_likelihood) / np.sum(weights))


def identified_threshold(lower: float, upper: float, horizon: int) -> bool | None:
    """Return event status by a horizon, or None when censoring leaves it unknown."""

    if upper <= horizon:
        return True
    if lower > horizon:
        return False
    return None


@dataclass(frozen=True, slots=True)
class HorizonDiagnostic:
    horizon_seconds: int
    brier_score: float | None
    maximum_calibration_error: float | None
    identified_row_count: int
    identified_weight: float
    calibration_bin_count: int


@dataclass(frozen=True, slots=True)
class ValidationDiagnostics:
    weighted_interval_negative_log_likelihood: float
    weighted_horizon_brier_score: float
    worst_supported_horizon_calibration_error: float
    horizons: tuple[HorizonDiagnostic, ...]


@dataclass(frozen=True, slots=True)
class _ThresholdRow:
    example_id: str
    anchor_id: str
    probability: float
    success: bool
    weight: float


def _equal_weight_bins(
    rows: list[_ThresholdRow], *, bin_count: int = 10
) -> list[list[_ThresholdRow]]:
    ordered = sorted(rows, key=lambda row: (row.probability, row.example_id.encode()))
    total_weight = math.fsum(row.weight for row in ordered)
    bins: list[list[_ThresholdRow]] = [[]]
    cumulative = 0.0
    for row in ordered:
        if len(bins) < bin_count and cumulative >= total_weight * len(bins) / bin_count:
            bins.append([])
        bins[-1].append(row)
        cumulative += row.weight
    return [values for values in bins if values]


def _merge_unsupported_bins(
    bins: list[list[_ThresholdRow]], *, minimum_distinct_anchors: int
) -> list[list[_ThresholdRow]]:
    while len(bins) > 1:
        index = next(
            (
                position
                for position, values in enumerate(bins)
                if len({row.anchor_id for row in values}) < minimum_distinct_anchors
            ),
            None,
        )
        if index is None:
            break
        if index < len(bins) - 1:
            bins[index + 1] = bins[index] + bins[index + 1]
            del bins[index]
        else:
            bins[index - 1].extend(bins[index])
            del bins[index]
    return bins


def validation_diagnostics(
    *,
    example_ids: tuple[str, ...],
    anchor_ids: tuple[str, ...],
    raw_margins: np.ndarray,
    lower_bounds: np.ndarray,
    upper_bounds: np.ndarray,
    weights: np.ndarray,
    horizons_seconds: tuple[int, ...],
    scale: float,
    distribution: AftDistribution,
    minimum_calibration_anchors: int = 200,
) -> ValidationDiagnostics:
    row_count = len(raw_margins)
    if len(example_ids) != row_count or len(anchor_ids) != row_count:
        raise ValueError("validation identifiers must align with predictions")
    probabilities = aft_cdf_matrix(
        raw_margins,
        horizons_seconds,
        scale=scale,
        distribution=distribution,
    )
    pooled_brier_numerator = 0.0
    pooled_brier_denominator = 0.0
    diagnostics: list[HorizonDiagnostic] = []
    for horizon_index, horizon in enumerate(horizons_seconds):
        rows: list[_ThresholdRow] = []
        for row_index in range(row_count):
            outcome = identified_threshold(
                float(lower_bounds[row_index]), float(upper_bounds[row_index]), horizon
            )
            if outcome is None:
                continue
            rows.append(
                _ThresholdRow(
                    example_ids[row_index],
                    anchor_ids[row_index],
                    float(probabilities[row_index, horizon_index]),
                    outcome,
                    float(weights[row_index]),
                )
            )
        identified_weight = math.fsum(row.weight for row in rows)
        if not rows:
            diagnostics.append(HorizonDiagnostic(horizon, None, None, 0, 0.0, 0))
            continue
        brier_numerator = math.fsum(
            row.weight * (row.probability - float(row.success)) ** 2 for row in rows
        )
        brier = brier_numerator / identified_weight
        pooled_brier_numerator += brier_numerator
        pooled_brier_denominator += identified_weight
        bins = _merge_unsupported_bins(
            _equal_weight_bins(rows), minimum_distinct_anchors=minimum_calibration_anchors
        )
        supported = [
            values
            for values in bins
            if len({row.anchor_id for row in values}) >= minimum_calibration_anchors
        ]
        gaps = []
        for values in supported:
            total = math.fsum(row.weight for row in values)
            predicted = math.fsum(row.weight * row.probability for row in values) / total
            observed = math.fsum(row.weight * float(row.success) for row in values) / total
            gaps.append(abs(predicted - observed))
        diagnostics.append(
            HorizonDiagnostic(
                horizon,
                brier,
                max(gaps) if gaps else None,
                len(rows),
                identified_weight,
                len(supported),
            )
        )
    calibration_values = [
        item.maximum_calibration_error
        for item in diagnostics
        if item.maximum_calibration_error is not None
    ]
    if pooled_brier_denominator <= 0 or not calibration_values:
        raise ValueError("validation diagnostics have insufficient identified support")
    return ValidationDiagnostics(
        weighted_interval_negative_log_likelihood=weighted_interval_nll(
            raw_margins,
            lower_bounds,
            upper_bounds,
            weights,
            scale=scale,
            distribution=distribution,
        ),
        weighted_horizon_brier_score=pooled_brier_numerator / pooled_brier_denominator,
        worst_supported_horizon_calibration_error=max(calibration_values),
        horizons=tuple(diagnostics),
    )
