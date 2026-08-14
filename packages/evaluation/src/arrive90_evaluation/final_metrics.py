"""Interval-aware final metrics, calibrated AFT scoring, and reliability tables."""

from __future__ import annotations

import bisect
import math
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
from arrive90_data_contracts.travel_time import DownstreamOutcomeState
from arrive90_models.distributions import AftDistribution
from arrive90_models.predictive_bundle import AftPredictiveBundle
from scipy.special import expit, logit, ndtr, ndtri  # type: ignore[import-untyped]

from arrive90_evaluation.aft_metrics import MINIMUM_LIKELIHOOD
from arrive90_evaluation.final_data import FinalEvaluationData


@dataclass(frozen=True, slots=True)
class FinalModelPredictions:
    bundle_id: str
    manifest_sha256: str
    distribution: str
    scale: float
    raw_margins: np.ndarray
    probabilities: np.ndarray
    quantiles_seconds: np.ndarray
    quantiles_resolved: np.ndarray

    def __post_init__(self) -> None:
        row_count = len(self.raw_margins)
        if (
            not self.bundle_id
            or self.probabilities.shape != (row_count, 7)
            or self.quantiles_seconds.shape != (row_count, 3)
            or self.quantiles_resolved.shape != (row_count, 3)
            or np.any(~np.isfinite(self.raw_margins))
            or np.any(~np.isfinite(self.probabilities))
            or np.any((self.probabilities < 0) | (self.probabilities > 1))
            or np.any(np.diff(self.probabilities, axis=1) < -1e-12)
        ):
            raise ValueError("final model predictions violate the frozen CDF contract")


def _standard_cdf(values: np.ndarray, distribution: AftDistribution) -> np.ndarray:
    if distribution is AftDistribution.NORMAL:
        return np.asarray(ndtr(values), dtype=np.float64)
    if distribution is AftDistribution.LOGISTIC:
        return np.asarray(expit(values), dtype=np.float64)
    exponent = np.exp(np.minimum(values, math.log(np.finfo(np.float64).max)))
    return np.asarray(-np.expm1(-exponent), dtype=np.float64)


def _standard_quantile(probabilities: np.ndarray, distribution: AftDistribution) -> np.ndarray:
    if distribution is AftDistribution.NORMAL:
        return np.asarray(ndtri(probabilities), dtype=np.float64)
    if distribution is AftDistribution.LOGISTIC:
        return np.asarray(logit(probabilities), dtype=np.float64)
    return np.log(-np.log1p(-probabilities))


def _calibrate(probabilities: np.ndarray, bundle: AftPredictiveBundle) -> np.ndarray:
    calibrated = np.empty_like(probabilities, dtype=np.float64)
    endpoints = (probabilities == 0) | (probabilities == 1)
    calibrated[endpoints] = probabilities[endpoints]
    interior = ~endpoints
    calibrated[interior] = expit(
        bundle.calibrator.positive_slope * logit(probabilities[interior])
        + bundle.calibrator.intercept
    )
    return np.maximum.accumulate(np.clip(calibrated, 0.0, 1.0), axis=1)


def predict_final_bundle(
    bundle: AftPredictiveBundle,
    features: Any,
    *,
    horizons_seconds: tuple[int, ...],
    quantiles: tuple[float, ...],
    model_horizon_seconds: int,
) -> FinalModelPredictions:
    """Score one registered bundle with vectorized calibrated CDF and quantiles."""

    if len(horizons_seconds) != 7 or quantiles != (0.5, 0.8, 0.9):
        raise ValueError("final prediction grid does not match the frozen protocol")
    margins = np.asarray(bundle.raw_margins(features), dtype=np.float64)
    horizons = np.asarray(horizons_seconds, dtype=np.float64)
    distribution = AftDistribution(bundle.manifest.aft_distribution)
    latent = (np.log(horizons)[None, :] - margins[:, None]) / bundle.manifest.aft_scale
    raw_probabilities = _standard_cdf(latent, distribution)
    probabilities = _calibrate(raw_probabilities, bundle)
    target = np.asarray(quantiles, dtype=np.float64)
    raw_targets = expit(
        (logit(target) - bundle.calibrator.intercept) / bundle.calibrator.positive_slope
    )
    standard = _standard_quantile(raw_targets, distribution)
    with np.errstate(over="ignore"):
        quantile_seconds = np.exp(margins[:, None] + bundle.manifest.aft_scale * standard[None, :])
    resolved = np.isfinite(quantile_seconds) & (quantile_seconds <= float(model_horizon_seconds))
    quantile_seconds = np.where(resolved, quantile_seconds, np.nan)
    return FinalModelPredictions(
        bundle_id=bundle.manifest.bundle_id,
        manifest_sha256=bundle.manifest.manifest_hash,
        distribution=bundle.manifest.aft_distribution,
        scale=bundle.manifest.aft_scale,
        raw_margins=margins,
        probabilities=probabilities,
        quantiles_seconds=quantile_seconds,
        quantiles_resolved=resolved,
    )


def _underlying_log_density(
    durations: np.ndarray,
    margins: np.ndarray,
    *,
    scale: float,
    distribution: AftDistribution,
) -> tuple[np.ndarray, np.ndarray]:
    latent = (np.log(durations) - margins) / scale
    if distribution is AftDistribution.NORMAL:
        standard = -0.5 * latent**2 - 0.5 * math.log(2.0 * math.pi)
    elif distribution is AftDistribution.LOGISTIC:
        standard = -np.logaddexp(0.0, -latent) - np.logaddexp(0.0, latent)
    else:
        standard = latent - np.exp(np.minimum(latent, math.log(np.finfo(np.float64).max)))
    return standard - math.log(scale) - np.log(durations), _standard_cdf(latent, distribution)


def calibrated_interval_nll_contributions(
    prediction: FinalModelPredictions,
    lower_bounds: np.ndarray,
    upper_bounds: np.ndarray,
    bundle: AftPredictiveBundle,
) -> np.ndarray:
    """Return per-row negative log likelihood under the calibrated final CDF."""

    row_count = len(prediction.raw_margins)
    if row_count == 0 or len(lower_bounds) != row_count or len(upper_bounds) != row_count:
        raise ValueError("calibrated likelihood arrays do not align")
    if (
        np.any(np.isnan(lower_bounds))
        or np.any(np.isnan(upper_bounds))
        or np.any(lower_bounds < 0)
        or np.any(upper_bounds <= 0)
        or np.any(upper_bounds < lower_bounds)
    ):
        raise ValueError("calibrated likelihood bounds are invalid")
    distribution = AftDistribution(prediction.distribution)
    margins = prediction.raw_margins
    exact = np.isfinite(upper_bounds) & (lower_bounds == upper_bounds)
    right = np.isinf(upper_bounds)
    interval = ~(exact | right)
    log_likelihood = np.empty(row_count, dtype=np.float64)
    if np.any(interval):
        upper_latent = (np.log(upper_bounds[interval]) - margins[interval]) / prediction.scale
        upper_raw = _standard_cdf(upper_latent, distribution)
        lower_raw = np.zeros(np.sum(interval), dtype=np.float64)
        positive = lower_bounds[interval] > 0
        lower_raw[positive] = _standard_cdf(
            (np.log(lower_bounds[interval][positive]) - margins[interval][positive])
            / prediction.scale,
            distribution,
        )
        calibrated = _calibrate(np.column_stack((lower_raw, upper_raw)), bundle)
        log_likelihood[interval] = np.log(
            np.maximum(calibrated[:, 1] - calibrated[:, 0], MINIMUM_LIKELIHOOD)
        )
    if np.any(right):
        lower_raw = _standard_cdf(
            (np.log(lower_bounds[right]) - margins[right]) / prediction.scale,
            distribution,
        )
        calibrated = _calibrate(lower_raw[:, None], bundle)[:, 0]
        log_likelihood[right] = np.log(np.maximum(1.0 - calibrated, MINIMUM_LIKELIHOOD))
    if np.any(exact):
        durations = lower_bounds[exact]
        if np.any(durations <= 0):
            raise ValueError("exact final durations must be positive")
        underlying_log_density, raw_cdf = _underlying_log_density(
            durations,
            margins[exact],
            scale=prediction.scale,
            distribution=distribution,
        )
        calibrated = _calibrate(raw_cdf[:, None], bundle)[:, 0]
        interior = np.clip(raw_cdf, 1e-15, 1.0 - 1e-15)
        derivative = (
            bundle.calibrator.positive_slope
            * calibrated
            * (1.0 - calibrated)
            / (interior * (1.0 - interior))
        )
        log_likelihood[exact] = underlying_log_density + np.log(
            np.maximum(derivative, MINIMUM_LIKELIHOOD)
        )
    return -log_likelihood


def threshold_status(
    lower_bounds: np.ndarray, upper_bounds: np.ndarray, horizon_seconds: int
) -> tuple[np.ndarray, np.ndarray]:
    """Return identified mask and binary event status for one horizon."""

    known = ~np.isnan(lower_bounds) & ~np.isnan(upper_bounds)
    success = known & (upper_bounds <= horizon_seconds)
    failure = known & (lower_bounds > horizon_seconds)
    return success | failure, success


def weighted_quantile(values: np.ndarray, weights: np.ndarray, probability: float) -> float:
    if (
        len(values) == 0
        or len(weights) != len(values)
        or not 0 < probability < 1
        or np.any(~np.isfinite(values))
        or np.any(~np.isfinite(weights))
        or np.any(weights <= 0)
    ):
        raise ValueError("weighted quantile inputs are invalid")
    order = np.argsort(values, kind="stable")
    ordered_values = values[order]
    ordered_weights = weights[order]
    threshold = probability * float(np.sum(ordered_weights))
    index = int(np.searchsorted(np.cumsum(ordered_weights), threshold, side="left"))
    return float(ordered_values[min(index, len(ordered_values) - 1)])


def _denominator(data: FinalEvaluationData, mask: np.ndarray) -> dict[str, float | int]:
    indices = np.flatnonzero(mask)
    rows = data.inventory.rows
    return {
        "analysis_weight": float(np.sum(data.analysis_weights[mask])),
        "distinct_anchor_count": len({rows[index].anchor_id for index in indices}),
        "distinct_service_day_count": len(
            {rows[index].service_date.isoformat() for index in indices}
        ),
        "raw_row_count": int(np.sum(mask)),
    }


def model_metric_summary(
    data: FinalEvaluationData,
    prediction: FinalModelPredictions,
    bundle: AftPredictiveBundle,
    *,
    horizons_seconds: tuple[int, ...],
    quantiles: tuple[float, ...],
    mask: np.ndarray | None = None,
) -> dict[str, Any]:
    """Compute every frozen primary metric over one row mask."""

    row_count = len(data.inventory.rows)
    selected = np.ones(row_count, dtype=np.bool_) if mask is None else mask
    if selected.shape != (row_count,) or not np.any(selected):
        raise ValueError("final metric mask is empty or invalid")
    weights = data.analysis_weights
    likelihood = selected & data.likelihood_mask
    nll: float | None = None
    if np.any(likelihood):
        nll_values = calibrated_interval_nll_contributions(
            FinalModelPredictions(
                prediction.bundle_id,
                prediction.manifest_sha256,
                prediction.distribution,
                prediction.scale,
                prediction.raw_margins[likelihood],
                prediction.probabilities[likelihood],
                prediction.quantiles_seconds[likelihood],
                prediction.quantiles_resolved[likelihood],
            ),
            data.lower_bounds[likelihood],
            data.upper_bounds[likelihood],
            bundle,
        )
        nll = float(np.sum(weights[likelihood] * nll_values) / np.sum(weights[likelihood]))
    horizons: list[dict[str, Any]] = []
    for horizon_index, horizon in enumerate(horizons_seconds):
        probabilities = prediction.probabilities[:, horizon_index]
        identified, success = threshold_status(data.lower_bounds, data.upper_bounds, horizon)
        identified &= selected
        unresolved = selected & ~identified
        losses_zero = probabilities**2
        losses_one = (1.0 - probabilities) ** 2
        identified_losses = np.where(success, losses_one, losses_zero)
        point_numerator = float(np.sum(weights[identified] * identified_losses[identified]))
        point_weight = float(np.sum(weights[identified]))
        lower_losses = np.minimum(losses_zero, losses_one)
        upper_losses = np.maximum(losses_zero, losses_one)
        lower_numerator = point_numerator + float(
            np.sum(weights[unresolved] * lower_losses[unresolved])
        )
        upper_numerator = point_numerator + float(
            np.sum(weights[unresolved] * upper_losses[unresolved])
        )
        total_weight = float(np.sum(weights[selected]))
        horizons.append(
            {
                "brier_complete_population_lower": lower_numerator / total_weight,
                "brier_complete_population_upper": upper_numerator / total_weight,
                "brier_identified": point_numerator / point_weight if point_weight else None,
                "horizon_seconds": horizon,
                "identified": _denominator(data, identified),
                "unresolved": _denominator(data, unresolved),
            }
        )
    quantile_rows: list[dict[str, Any]] = []
    finite = selected & data.finite_upper_mask
    for quantile_index, probability in enumerate(quantiles):
        resolved = finite & prediction.quantiles_resolved[:, quantile_index]
        values = prediction.quantiles_seconds[:, quantile_index]
        lower = data.lower_bounds
        upper = data.upper_bounds
        distance = np.maximum(np.maximum(lower - values, values - upper), 0.0)
        below_lower = values < lower
        above_upper = values > upper
        pinball_lower = np.where(
            below_lower,
            probability * (lower - values),
            np.where(above_upper, (1.0 - probability) * (values - upper), 0.0),
        )
        loss_at_lower = np.where(
            lower >= values,
            probability * (lower - values),
            (1.0 - probability) * (values - lower),
        )
        loss_at_upper = np.where(
            upper >= values,
            probability * (upper - values),
            (1.0 - probability) * (values - upper),
        )
        pinball_upper = np.maximum(loss_at_lower, loss_at_upper)
        coverage_lower = upper <= values
        coverage_upper = lower <= values
        weight = float(np.sum(weights[resolved]))
        quantile_rows.append(
            {
                "coverage_lower": (
                    float(np.sum(weights[resolved] * coverage_lower[resolved])) / weight
                    if weight
                    else None
                ),
                "coverage_upper": (
                    float(np.sum(weights[resolved] * coverage_upper[resolved])) / weight
                    if weight
                    else None
                ),
                "mean_absolute_interval_distance_seconds": (
                    float(np.sum(weights[resolved] * distance[resolved]) / weight)
                    if weight
                    else None
                ),
                "median_absolute_interval_distance_seconds": (
                    weighted_quantile(distance[resolved], weights[resolved], 0.5)
                    if weight
                    else None
                ),
                "pinball_loss_lower": (
                    float(np.sum(weights[resolved] * pinball_lower[resolved]) / weight)
                    if weight
                    else None
                ),
                "pinball_loss_upper": (
                    float(np.sum(weights[resolved] * pinball_upper[resolved]) / weight)
                    if weight
                    else None
                ),
                "probability": probability,
                "resolved_finite_upper": _denominator(data, resolved),
                "unresolved_or_censored": _denominator(data, selected & ~resolved),
                "zero_lower_left_censoring_treatment": (
                    "finite interval bounds retained; distance is zero inside the interval"
                ),
            }
        )
    interval_resolved = (
        selected & prediction.quantiles_resolved[:, 0] & prediction.quantiles_resolved[:, 2]
    )
    widths = prediction.quantiles_seconds[:, 2] - prediction.quantiles_seconds[:, 0]
    interval_weight = float(np.sum(weights[interval_resolved]))
    return {
        "availability": {
            "all_selected": _denominator(data, selected),
            "likelihood": _denominator(data, likelihood),
            "prediction_interval_resolved": _denominator(data, interval_resolved),
        },
        "bundle_id": prediction.bundle_id,
        "horizons": horizons,
        "interval_negative_log_likelihood": nll,
        "prediction_interval_width_seconds": {
            "definition": "p90 minus p50",
            "mean": (
                float(np.sum(weights[interval_resolved] * widths[interval_resolved]))
                / interval_weight
                if interval_weight
                else None
            ),
            "p95": (
                weighted_quantile(widths[interval_resolved], weights[interval_resolved], 0.95)
                if interval_weight
                else None
            ),
        },
        "quantiles": quantile_rows,
    }


def calibration_table(
    data: FinalEvaluationData,
    probabilities: np.ndarray,
    *,
    horizon_seconds: int,
    initial_bin_count: int,
    minimum_distinct_anchors: int,
) -> dict[str, Any]:
    """Build deterministic identified calibration bins and population bounds."""

    if len(probabilities) != len(data.inventory.rows):
        raise ValueError("calibration probabilities do not align with final rows")
    identified, success = threshold_status(data.lower_bounds, data.upper_bounds, horizon_seconds)
    identified_indices = [int(value) for value in np.flatnonzero(identified)]
    if len(identified_indices) == 0:
        return {
            "bins": [],
            "expected_calibration_error": None,
            "horizon_seconds": horizon_seconds,
            "maximum_calibration_error": None,
            "supported": False,
        }
    rows = data.inventory.rows
    weights = data.analysis_weights
    ordered = sorted(
        identified_indices,
        key=lambda index: (probabilities[index], rows[index].example_id.encode()),
    )
    total_identified_weight = math.fsum(float(weights[index]) for index in ordered)
    bins: list[list[int]] = [[]]
    cumulative = 0.0
    for index in ordered:
        if (
            len(bins) < initial_bin_count
            and cumulative >= total_identified_weight * len(bins) / initial_bin_count
        ):
            bins.append([])
        bins[-1].append(index)
        cumulative += float(weights[index])
    while len(bins) > 1:
        unsupported = next(
            (
                position
                for position, values in enumerate(bins)
                if len({rows[index].anchor_id for index in values}) < minimum_distinct_anchors
            ),
            None,
        )
        if unsupported is None:
            break
        if unsupported < len(bins) - 1:
            bins[unsupported + 1] = bins[unsupported] + bins[unsupported + 1]
            del bins[unsupported]
        else:
            bins[unsupported - 1].extend(bins[unsupported])
            del bins[unsupported]
    cutoffs = [(probabilities[values[-1]], rows[values[-1]].example_id) for values in bins[:-1]]
    all_bins: list[list[int]] = [[] for _ in bins]
    for row_index, row in enumerate(rows):
        key = (float(probabilities[row_index]), row.example_id)
        bin_position = bisect.bisect_left(cutoffs, key)
        all_bins[bin_position].append(row_index)
    payload: list[dict[str, Any]] = []
    ece_numerator = 0.0
    supported_gaps: list[float] = []
    for bin_index, identified_members in enumerate(bins):
        all_members = all_bins[bin_index]
        identified_mask = np.zeros(len(rows), dtype=np.bool_)
        identified_mask[identified_members] = True
        total_mask = np.zeros(len(rows), dtype=np.bool_)
        total_mask[all_members] = True
        identified_weight = math.fsum(float(weights[index]) for index in identified_members)
        all_weight = math.fsum(float(weights[index]) for index in all_members)
        mean_probability = (
            math.fsum(float(weights[index] * probabilities[index]) for index in identified_members)
            / identified_weight
        )
        observed_rate = (
            math.fsum(float(weights[index] * success[index]) for index in identified_members)
            / identified_weight
        )
        gap = abs(mean_probability - observed_rate)
        supported = (
            len({rows[index].anchor_id for index in identified_members}) >= minimum_distinct_anchors
        )
        if supported:
            ece_numerator += identified_weight * gap
            supported_gaps.append(gap)
        known_positive_weight = math.fsum(
            float(weights[index]) for index in all_members if identified[index] and success[index]
        )
        unresolved_weight = math.fsum(
            float(weights[index]) for index in all_members if not identified[index]
        )
        payload.append(
            {
                "bin_index": bin_index,
                "identified": _denominator(data, identified_mask),
                "maximum_probability": float(max(probabilities[index] for index in all_members)),
                "mean_predicted_probability": mean_probability,
                "minimum_probability": float(min(probabilities[index] for index in all_members)),
                "observed_success_rate": observed_rate,
                "population_success_rate_lower": known_positive_weight / all_weight,
                "population_success_rate_upper": (known_positive_weight + unresolved_weight)
                / all_weight,
                "supported": supported,
                "total": _denominator(data, total_mask),
            }
        )
    supported_weight = math.fsum(
        float(weights[index])
        for values, item in zip(bins, payload, strict=True)
        if item["supported"]
        for index in values
    )
    return {
        "bins": payload,
        "expected_calibration_error": (
            ece_numerator / supported_weight if supported_weight else None
        ),
        "horizon_seconds": horizon_seconds,
        "maximum_calibration_error": max(supported_gaps) if supported_gaps else None,
        "supported": bool(supported_gaps),
    }


def outcome_mass_report(data: FinalEvaluationData) -> dict[str, Any]:
    """Report every selected outcome state and complete-population audit count."""

    selected: dict[str, Any] = {}
    states = sorted({item.value for item in DownstreamOutcomeState}, key=str.encode)
    for state in states:
        mask = np.asarray([value == state for value in data.outcome_states], dtype=np.bool_)
        selected[state] = _denominator(data, mask)
    complete_counts: dict[str, int] = dict.fromkeys(states, 0)
    for day in data.inventory.context.unsampled_manifest["daily_partitions"]:
        if day["split"] != "FINAL_TEST":
            continue
        for state, count in day["audit_projection"]["outcome_state_counts"].items():
            complete_counts[str(state)] = complete_counts.get(str(state), 0) + int(count)
    quarantined_states = (
        DownstreamOutcomeState.SCHEDULE_UNMATCHED.value,
        DownstreamOutcomeState.SESSION_DISCONTINUITY.value,
    )
    return {
        "complete_population_raw_counts": dict(sorted(complete_counts.items())),
        "quarantined_raw_count": sum(complete_counts[state] for state in quarantined_states),
        "quarantined_states": list(quarantined_states),
        "selected_population": selected,
    }


def interval_to_payload(interval: Any) -> dict[str, Any]:
    return asdict(interval)
