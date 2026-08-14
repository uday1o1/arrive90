"""Deterministic final tables, uncertainty, claims, and failure evidence."""

from __future__ import annotations

import hashlib
import math
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
from arrive90_data_contracts.gates import DEFAULT_ACCEPTANCE_VERSION
from arrive90_outcomes.travel_time_baselines import EmpiricalMidpointQuery
from scipy import sparse  # type: ignore[import-untyped]

from arrive90_evaluation.final_artifacts import (
    LoadedModelRegistry,
    canonical_json,
    file_sha256,
)
from arrive90_evaluation.final_bootstrap import (
    ServiceDayBootstrapPlan,
    bootstrap_weighted_quantile,
    bootstrap_weighted_ratio,
    bootstrap_weighted_ratio_difference,
    build_service_day_bootstrap_plan,
)
from arrive90_evaluation.final_data import (
    FinalEvaluationData,
    FinalFeatureInventory,
    FinalFeatureRow,
    FinalTestAccess,
)
from arrive90_evaluation.final_metrics import (
    FinalModelPredictions,
    calibrated_interval_nll_contributions,
    calibration_table,
    model_metric_summary,
    outcome_mass_report,
    threshold_status,
)
from arrive90_evaluation.modeling_data import ModelingContext


def _column(table: pa.Table, name: str) -> np.ndarray:
    return np.asarray(table[name].to_numpy(zero_copy_only=False))


def _nullable_float(table: pa.Table, name: str) -> np.ndarray:
    return np.asarray(
        [math.nan if value is None else float(value) for value in table[name].to_pylist()],
        dtype=np.float64,
    )


def data_from_prediction_table(
    context: ModelingContext,
    manifest: dict[str, Any],
    table: pa.Table,
) -> FinalEvaluationData:
    """Rebuild metric inputs without reopening a final outcome partition."""

    slice_names = tuple(str(value) for value in manifest["slice_names"])
    source_hashes = [str(value) for value in table["source_example_sha256"].to_pylist()]
    anchor_hashes = [str(value) for value in table["anchor_sha256"].to_pylist()]
    service_dates = table["service_date"].to_pylist()
    weights = _column(table, "analysis_weight").astype(np.float64)
    rows: list[FinalFeatureRow] = []
    for index, source_hash in enumerate(source_hashes):
        service_date = service_dates[index]
        slices = tuple(
            sorted(
                ((name, str(table[f"slice__{name}"][index].as_py())) for name in slice_names),
                key=lambda item: item[0].encode(),
            )
        )
        day_type = dict(slices)["day_type"]
        rows.append(
            FinalFeatureRow(
                example_id=source_hash,
                source_example_sha256=source_hash,
                anchor_id=anchor_hashes[index],
                service_date=service_date,
                analysis_weight=float(weights[index]),
                query=EmpiricalMidpointQuery(
                    anchor_id=anchor_hashes[index],
                    route_id="Blue",
                    direction_id="0",
                    origin_stop_id="REDACTED",
                    destination_stop_id="REDACTED",
                    destination_offset=1,
                    day_type=day_type,
                    time_bucket="00:00-03:00",
                ),
                feature_values=(),
                slices=slices,
            )
        )
    inventory = FinalFeatureInventory(
        context=context,
        features=sparse.csr_matrix(
            (len(rows), len(context.feature_transform.column_names)), dtype=np.float32
        ),
        rows=tuple(rows),
        service_dates=tuple(sorted({value.isoformat() for value in service_dates})),
        row_manifest_sha256=str(manifest["row_manifest_sha256"]),
    )
    return FinalEvaluationData(
        inventory=inventory,
        outcome_states=tuple(str(value) for value in table["outcome_state"].to_pylist()),
        lower_bounds=_nullable_float(table, "lower_bound_seconds"),
        upper_bounds=_nullable_float(table, "upper_bound_seconds"),
        outcome_manifest_sha256=str(manifest["outcome_manifest_sha256"]),
        access=FinalTestAccess(
            protocol_sha256=str(manifest["protocol_sha256"]),
            replay_selection_sha256=str(manifest["replay_selection_sha256"]),
        ),
    )


def predictions_from_table(
    manifest: dict[str, Any], table: pa.Table
) -> dict[str, FinalModelPredictions]:
    predictions: dict[str, FinalModelPredictions] = {}
    for bundle_id in manifest["model_order"]:
        metadata = manifest["model_columns"][bundle_id]
        prefix = str(metadata["prefix"])
        probabilities = np.column_stack(
            [
                _column(table, f"{prefix}__p_{horizon}")
                for horizon in (300, 600, 900, 1200, 1800, 2700, 3600)
            ]
        ).astype(np.float64)
        quantiles = np.column_stack(
            [_nullable_float(table, f"{prefix}__q_{quantile}") for quantile in (50, 80, 90)]
        )
        resolved = np.column_stack(
            [_column(table, f"{prefix}__q_{quantile}_resolved") for quantile in (50, 80, 90)]
        ).astype(np.bool_)
        predictions[str(bundle_id)] = FinalModelPredictions(
            bundle_id=str(bundle_id),
            manifest_sha256=str(metadata["manifest_sha256"]),
            distribution=str(metadata["distribution"]),
            scale=float(metadata["scale"]),
            raw_margins=_column(table, f"{prefix}__raw_margin").astype(np.float64),
            probabilities=probabilities,
            quantiles_seconds=quantiles,
            quantiles_resolved=resolved,
        )
    return predictions


def _day_indices(data: FinalEvaluationData, plan: ServiceDayBootstrapPlan) -> np.ndarray:
    mapping = {service_date: index for index, service_date in enumerate(plan.service_dates)}
    return np.asarray(
        [mapping[row.service_date.isoformat()] for row in data.inventory.rows], dtype=np.int16
    )


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


def _model_uncertainty(
    data: FinalEvaluationData,
    predictions: dict[str, FinalModelPredictions],
    registry: LoadedModelRegistry,
    plan: ServiceDayBootstrapPlan,
    day_indices: np.ndarray,
    horizons: tuple[int, ...],
) -> dict[str, Any]:
    weights = data.analysis_weights
    result: dict[str, Any] = {}
    for bundle_id, prediction in predictions.items():
        bundle = registry.bundles[bundle_id]
        likelihood = data.likelihood_mask
        nll = calibrated_interval_nll_contributions(
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
        nll_interval = bootstrap_weighted_ratio(
            weights[likelihood] * nll,
            weights[likelihood],
            day_indices[likelihood],
            plan,
        )
        brier: list[dict[str, Any]] = []
        for horizon_index, horizon in enumerate(horizons):
            identified, success = threshold_status(data.lower_bounds, data.upper_bounds, horizon)
            loss = (prediction.probabilities[:, horizon_index] - success.astype(np.float64)) ** 2
            interval = bootstrap_weighted_ratio(
                weights[identified] * loss[identified],
                weights[identified],
                day_indices[identified],
                plan,
            )
            brier.append({"horizon_seconds": horizon, **asdict(interval)})
        result[bundle_id] = {
            "horizon_brier_identified": brier,
            "interval_negative_log_likelihood": asdict(nll_interval),
        }
    return result


def _point_diagnostics(
    data: FinalEvaluationData,
    table: pa.Table,
    promoted: FinalModelPredictions,
    plan: ServiceDayBootstrapPlan,
    day_indices: np.ndarray,
) -> dict[str, Any]:
    weights = data.analysis_weights
    finite = data.finite_upper_mask
    points = {
        "EMPIRICAL_MIDPOINT": _nullable_float(table, "empirical_midpoint_seconds"),
        "OFFICIAL_SCHEDULE": _column(table, "official_schedule_seconds").astype(np.float64),
        "PROMOTED_P50": promoted.quantiles_seconds[:, 0],
    }
    diagnostics: dict[str, Any] = {}
    distances: dict[str, np.ndarray] = {}
    masks: dict[str, np.ndarray] = {}
    for name, point in points.items():
        mask = finite & np.isfinite(point)
        distance = np.maximum(np.maximum(data.lower_bounds - point, point - data.upper_bounds), 0.0)
        distances[name] = distance
        masks[name] = mask
        mean = bootstrap_weighted_ratio(
            weights[mask] * distance[mask],
            weights[mask],
            day_indices[mask],
            plan,
        )
        median = bootstrap_weighted_quantile(
            distance[mask],
            weights[mask],
            day_indices[mask],
            plan,
            probability=0.5,
        )
        diagnostics[name] = {
            "excluded_censored_or_unavailable": _denominator(data, ~mask),
            "mean_absolute_interval_distance_seconds": asdict(mean),
            "median_absolute_interval_distance_seconds": asdict(median),
            "metric_eligible": _denominator(data, mask),
        }
    comparisons: dict[str, Any] = {}
    for baseline in ("OFFICIAL_SCHEDULE", "EMPIRICAL_MIDPOINT"):
        common = masks["PROMOTED_P50"] & masks[baseline]
        difference = bootstrap_weighted_ratio_difference(
            weights[common] * distances["PROMOTED_P50"][common],
            weights[common] * distances[baseline][common],
            weights[common],
            day_indices[common],
            plan,
        )
        comparisons[f"PROMOTED_P50_MINUS_{baseline}"] = {
            "common_rows": _denominator(data, common),
            "mean_absolute_interval_distance_difference_seconds": asdict(difference),
            "negative_favors_promoted": True,
        }
    return {"comparisons": comparisons, "models": diagnostics}


def _slice_tables(
    data: FinalEvaluationData,
    prediction: FinalModelPredictions,
    registry: LoadedModelRegistry,
    config: dict[str, Any],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    horizons = tuple(int(value) for value in config["horizons_seconds"])
    quantiles = tuple(float(value) for value in config["quantiles"])
    rows = data.inventory.rows
    for dimension in config["slice_dimensions"]:
        if dimension == "outcome_class":
            levels = sorted(set(data.outcome_states), key=str.encode)
            values = data.outcome_states
        else:
            values = tuple(row.slice_values[str(dimension)] for row in rows)
            levels = sorted(set(values), key=str.encode)
        result[str(dimension)] = {
            level: model_metric_summary(
                data,
                prediction,
                registry.promoted,
                horizons_seconds=horizons,
                quantiles=quantiles,
                mask=np.asarray([value == level for value in values], dtype=np.bool_),
            )
            for level in levels
        }
    return result


def _drift_report(
    data: FinalEvaluationData,
    prediction: FinalModelPredictions,
    registry: LoadedModelRegistry,
    config: dict[str, Any],
) -> dict[str, Any]:
    months: dict[str, Any] = {}
    for month in ("2024-11", "2024-12"):
        mask = np.asarray(
            [row.service_date.strftime("%Y-%m") == month for row in data.inventory.rows],
            dtype=np.bool_,
        )
        summary = model_metric_summary(
            data,
            prediction,
            registry.promoted,
            horizons_seconds=tuple(int(value) for value in config["horizons_seconds"]),
            quantiles=tuple(float(value) for value in config["quantiles"]),
            mask=mask,
        )
        months[month] = {
            "metrics": summary,
            "mean_predicted_probability_15m": float(
                np.average(prediction.probabilities[mask, 2], weights=data.analysis_weights[mask])
            ),
        }
    return {
        "comparison": "December minus November",
        "interval_nll_difference": (
            months["2024-12"]["metrics"]["interval_negative_log_likelihood"]
            - months["2024-11"]["metrics"]["interval_negative_log_likelihood"]
        ),
        "months": months,
        "predicted_probability_15m_difference": (
            months["2024-12"]["mean_predicted_probability_15m"]
            - months["2024-11"]["mean_predicted_probability_15m"]
        ),
    }


def _failure_cases(
    data: FinalEvaluationData,
    prediction: FinalModelPredictions,
    *,
    count: int,
) -> list[dict[str, Any]]:
    points = prediction.quantiles_seconds[:, 0]
    eligible = data.finite_upper_mask & np.isfinite(points)
    distance = np.maximum(np.maximum(data.lower_bounds - points, points - data.upper_bounds), 0.0)
    indices = sorted(
        np.flatnonzero(eligible),
        key=lambda index: (-distance[index], data.inventory.rows[index].source_example_sha256),
    )[:count]
    return [
        {
            "absolute_interval_distance_seconds": float(distance[index]),
            "lower_bound_seconds": float(data.lower_bounds[index]),
            "outcome_state": data.outcome_states[index],
            "p50_seconds": float(points[index]),
            "service_date": data.inventory.rows[index].service_date.isoformat(),
            "slices": data.inventory.rows[index].slice_values,
            "source_example_sha256": data.inventory.rows[index].source_example_sha256,
            "upper_bound_seconds": float(data.upper_bounds[index]),
        }
        for index in indices
    ]


def build_final_report(
    context: ModelingContext,
    config: dict[str, Any],
    registry: LoadedModelRegistry,
    prediction_manifest: dict[str, Any],
    prediction_table: pa.Table,
    *,
    protocol: dict[str, Any],
    demo_artifacts: dict[str, Any],
) -> dict[str, Any]:
    """Reproduce every deterministic final table from the frozen prediction file."""

    data = data_from_prediction_table(context, prediction_manifest, prediction_table)
    predictions = predictions_from_table(prediction_manifest, prediction_table)
    promoted = predictions[registry.promoted_bundle_id]
    horizons = tuple(int(value) for value in config["horizons_seconds"])
    quantiles = tuple(float(value) for value in config["quantiles"])
    bootstrap_config = config["bootstrap"]
    plan = build_service_day_bootstrap_plan(
        data.inventory.service_dates,
        seed=int(bootstrap_config["seed"]),
        replicates=int(bootstrap_config["replicates"]),
    )
    day_indices = _day_indices(data, plan)
    model_metrics = {
        bundle_id: model_metric_summary(
            data,
            prediction,
            registry.bundles[bundle_id],
            horizons_seconds=horizons,
            quantiles=quantiles,
        )
        for bundle_id, prediction in predictions.items()
    }
    calibration = {
        bundle_id: [
            calibration_table(
                data,
                prediction.probabilities[:, horizon_index],
                horizon_seconds=horizon,
                initial_bin_count=int(config["calibration"]["initial_equal_analysis_weight_bins"]),
                minimum_distinct_anchors=int(
                    config["calibration"]["minimum_distinct_anchors_per_bin"]
                ),
            )
            for horizon_index, horizon in enumerate(horizons)
        ]
        for bundle_id, prediction in predictions.items()
    }
    point = _point_diagnostics(data, prediction_table, promoted, plan, day_indices)
    uncertainty = _model_uncertainty(data, predictions, registry, plan, day_indices, horizons)
    p50 = promoted.quantiles_seconds[:, 0]
    p50_mask = data.finite_upper_mask & np.isfinite(p50)
    p50_distance = np.maximum(np.maximum(data.lower_bounds - p50, p50 - data.upper_bounds), 0.0)
    uncertainty[registry.promoted_bundle_id]["p50_median_absolute_interval_distance_seconds"] = (
        asdict(
            bootstrap_weighted_quantile(
                p50_distance[p50_mask],
                data.analysis_weights[p50_mask],
                day_indices[p50_mask],
                plan,
                probability=0.5,
            )
        )
    )
    strongest_baseline = min(
        ("INTERCEPT_ONLY-normal", "SCHEDULE_CALENDAR-normal"),
        key=lambda name: model_metrics[name]["interval_negative_log_likelihood"],
    )
    negative_results = {
        "learned_model_underperforms_strongest_aft_baseline": (
            model_metrics[registry.promoted_bundle_id]["interval_negative_log_likelihood"]
            > model_metrics[strongest_baseline]["interval_negative_log_likelihood"]
        ),
        "promoted_bundle_id": registry.promoted_bundle_id,
        "strongest_aft_baseline_id": strongest_baseline,
        "strongest_baseline_interval_nll": model_metrics[strongest_baseline][
            "interval_negative_log_likelihood"
        ],
        "promoted_interval_nll": model_metrics[registry.promoted_bundle_id][
            "interval_negative_log_likelihood"
        ],
        "underperformance_is_retained_without_post_test_reselection": True,
    }
    return {
        "acceptance_version": DEFAULT_ACCEPTANCE_VERSION,
        "ablations": {
            name: model_metrics[name]
            for name in ("NO_PREFIX_HISTORY-normal", "NO_POSITION_OBSERVATION-normal")
        },
        "availability": outcome_mass_report(data),
        "bootstrap": {
            **bootstrap_config,
            "plan_sha256": plan.manifest_sha256,
            "service_day_block_count": len(plan.service_dates),
        },
        "calibration": calibration,
        "demo_artifacts": demo_artifacts,
        "drift": _drift_report(data, promoted, registry, config),
        "failure_cases": _failure_cases(data, promoted, count=int(config["failure_case_count"])),
        "final_test": {
            "end_date": config["final_test"]["end_date"],
            "outcome_manifest_sha256": data.outcome_manifest_sha256,
            "prediction_manifest_sha256": value_sha256_without_path(prediction_manifest),
            "prediction_sha256": prediction_manifest["prediction_sha256"],
            "row_count": len(data.inventory.rows),
            "row_manifest_sha256": data.inventory.row_manifest_sha256,
            "split": "FINAL_TEST",
            "start_date": config["final_test"]["start_date"],
        },
        "metric_definitions": {
            "calibration": config["calibration"],
            "horizons_seconds": list(horizons),
            "prediction_interval": "p90 minus p50",
            "quantiles": list(quantiles),
            "zero_lower_left_censoring": (
                "retained as a finite interval from zero through the observed upper bound"
            ),
        },
        "models": model_metrics,
        "negative_results": negative_results,
        "point_diagnostics": point,
        "protocol_sha256": value_sha256_without_path(protocol),
        "slice_tables": _slice_tables(data, promoted, registry, config),
        "uncertainty": uncertainty,
        "version": "travel-time-final-report-v1",
    }


def value_sha256_without_path(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def build_claim_registry(report: dict[str, Any], report_sha256: str) -> dict[str, Any]:
    """Generate narrow public numeric claims linked to one immutable final report."""

    promoted_id = str(report["negative_results"]["promoted_bundle_id"])
    promoted = report["models"][promoted_id]
    nll_interval = report["uncertainty"][promoted_id]["interval_negative_log_likelihood"]
    horizon = next(item for item in promoted["horizons"] if item["horizon_seconds"] == 900)
    horizon_interval = next(
        item
        for item in report["uncertainty"][promoted_id]["horizon_brier_identified"]
        if item["horizon_seconds"] == 900
    )
    schedule = report["point_diagnostics"]["comparisons"]["PROMOTED_P50_MINUS_OFFICIAL_SCHEDULE"][
        "mean_absolute_interval_distance_difference_seconds"
    ]
    empirical = report["point_diagnostics"]["comparisons"]["PROMOTED_P50_MINUS_EMPIRICAL_MIDPOINT"][
        "mean_absolute_interval_distance_difference_seconds"
    ]
    p90 = next(item for item in promoted["quantiles"] if item["probability"] == 0.9)
    claims = [
        {
            "artifact_sha256": report_sha256,
            "baseline": None,
            "confidence_interval": nll_interval,
            "metric": "calibrated interval negative log likelihood",
            "model": promoted_id,
            "report_pointer": f"/models/{promoted_id}/interval_negative_log_likelihood",
            "slice": "OVERALL",
            "split": "FINAL_TEST",
            "value": promoted["interval_negative_log_likelihood"],
        },
        {
            "artifact_sha256": report_sha256,
            "baseline": None,
            "confidence_interval": horizon_interval,
            "metric": "identified 15-minute Brier score",
            "model": promoted_id,
            "report_pointer": f"/models/{promoted_id}/horizons/2/brier_identified",
            "slice": "OVERALL",
            "split": "FINAL_TEST",
            "value": horizon["brier_identified"],
        },
        {
            "artifact_sha256": report_sha256,
            "baseline": "OFFICIAL_SCHEDULE",
            "confidence_interval": schedule,
            "metric": "mean absolute interval-distance difference in seconds",
            "model": promoted_id,
            "report_pointer": (
                "/point_diagnostics/comparisons/"
                "PROMOTED_P50_MINUS_OFFICIAL_SCHEDULE/"
                "mean_absolute_interval_distance_difference_seconds/estimate"
            ),
            "slice": "COMMON_FINITE_UPPER_ROWS",
            "split": "FINAL_TEST",
            "value": schedule["estimate"],
        },
        {
            "artifact_sha256": report_sha256,
            "baseline": "EMPIRICAL_MIDPOINT",
            "confidence_interval": empirical,
            "metric": "mean absolute interval-distance difference in seconds",
            "model": promoted_id,
            "report_pointer": (
                "/point_diagnostics/comparisons/"
                "PROMOTED_P50_MINUS_EMPIRICAL_MIDPOINT/"
                "mean_absolute_interval_distance_difference_seconds/estimate"
            ),
            "slice": "COMMON_FINITE_UPPER_ROWS",
            "split": "FINAL_TEST",
            "value": empirical["estimate"],
        },
        {
            "artifact_sha256": report_sha256,
            "baseline": None,
            "confidence_interval": None,
            "metric": "p90 empirical coverage bounds",
            "model": promoted_id,
            "report_pointer": f"/models/{promoted_id}/quantiles/2",
            "slice": "FINITE_UPPER_AND_RESOLVED_P90",
            "split": "FINAL_TEST",
            "value": {
                "lower": p90["coverage_lower"],
                "upper": p90["coverage_upper"],
            },
        },
    ]
    return {
        "acceptance_version": DEFAULT_ACCEPTANCE_VERSION,
        "claims": claims,
        "final_report_sha256": report_sha256,
        "negative_results_retained": True,
        "version": "travel-time-public-claims-v1",
    }


def report_sha256(path: Path) -> str:
    return file_sha256(path)
