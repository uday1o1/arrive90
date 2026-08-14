from __future__ import annotations

import hashlib
import math
from dataclasses import replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import numpy as np
import pytest
from arrive90_evaluation.final_bootstrap import BootstrapInterval
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
    interval_to_payload,
    model_metric_summary,
    outcome_mass_report,
    predict_final_bundle,
    threshold_status,
    weighted_quantile,
)
from arrive90_evaluation.modeling_data import ModelingContext
from arrive90_features.transform import FeatureTransformInput, fit_feature_transform
from arrive90_features.travel_time_registry import TRAVEL_TIME_V1_REGISTRY
from arrive90_models.calibration import SigmoidCalibrator
from arrive90_models.distributions import AftDistribution
from arrive90_models.predictive_bundle import AftPredictiveBundle
from arrive90_models.registry import ModelBundleManifest
from arrive90_models.xgb_aft import AftTrainingConfig, train_aft_model
from arrive90_outcomes.travel_time_baselines import EmpiricalMidpointQuery
from scipy import sparse  # type: ignore[import-untyped]


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _values() -> dict[str, str | int | float | bool | None]:
    values: dict[str, str | int | float | bool | None] = {}
    for name, spec in TRAVEL_TIME_V1_REGISTRY.specs.items():
        if spec.value_type == "categorical":
            values[name] = {
                "route_id": "Blue",
                "direction_id": "0",
                "origin_stop_id": "origin",
                "destination_stop_id": "destination",
                "route_pattern_id": "pattern",
            }[name]
        elif spec.value_type == "boolean":
            values[name] = False
        elif spec.value_type == "integer":
            values[name] = 1
        else:
            values[name] = 1.0
    values["scheduled_remaining_seconds"] = 300.0
    return values


def _context() -> ModelingContext:
    transform = fit_feature_transform((FeatureTransformInput("training", _values()),))
    audit_days = [
        {
            "audit_projection": {
                "outcome_state_counts": {
                    "INTERVAL_RESOLVED": 1,
                    "SCHEDULE_UNMATCHED": 1,
                    "SESSION_DISCONTINUITY": 1,
                }
            },
            "split": "FINAL_TEST",
        }
    ]
    return ModelingContext(
        dataset_root=Path("dataset"),
        population_manifest={},
        population_manifest_sha256=_hash("population"),
        unsampled_manifest={"daily_partitions": audit_days},
        unsampled_manifest_sha256=_hash("unsampled"),
        normalized_manifest_sha256=_hash("normalized"),
        source_lock_sha256=_hash("source"),
        feature_transform=transform,
        feature_transform_sha256=_hash("transform"),
        split_manifest_sha256=_hash("split"),
    )


def _data() -> FinalEvaluationData:
    context = _context()
    states = (
        "INTERVAL_RESOLVED",
        "LEFT_CENSORED",
        "RIGHT_CENSORED",
        "OVER_WIDTH_INTERVAL",
        "MISSING_STOP_OBSERVATION",
        "SESSION_DISCONTINUITY",
        "SCHEDULE_UNMATCHED",
        "NO_FOLLOW_UP",
    )
    lower = np.asarray([100, 0, 200, 100, math.nan, math.nan, math.nan, math.nan] * 2)
    upper = np.asarray([120, 50, math.inf, 400, math.nan, math.nan, math.nan, math.nan] * 2)
    rows = tuple(
        FinalFeatureRow(
            example_id=f"example-{index}",
            source_example_sha256=_hash(f"example-{index}"),
            anchor_id=f"anchor-{index}",
            service_date=(date(2024, 11, 1) if index < 8 else date(2024, 12, 1)),
            analysis_weight=1.0 + index / 100,
            query=EmpiricalMidpointQuery(
                anchor_id=f"anchor-{index}",
                route_id="Blue",
                direction_id="0",
                origin_stop_id="origin",
                destination_stop_id="destination",
                destination_offset=1,
                day_type="WEEKDAY",
                time_bucket="00:00-03:00",
            ),
            feature_values=tuple(_values().items()),
            slices=(
                ("day_type", "WEEKDAY"),
                ("destination_class", "IMMEDIATE"),
            ),
        )
        for index in range(16)
    )
    matrix = np.zeros((16, len(context.feature_transform.column_names)), dtype=np.float32)
    matrix[:, 0] = np.arange(16)
    inventory = FinalFeatureInventory(
        context,
        sparse.csr_matrix(matrix),
        rows,
        ("2024-11-01", "2024-12-01"),
        _hash("rows"),
    )
    return FinalEvaluationData(
        inventory,
        states * 2,
        lower.astype(np.float64),
        upper.astype(np.float64),
        _hash("outcomes"),
        FinalTestAccess(_hash("protocol"), _hash("replay")),
    )


def _bundle(data: FinalEvaluationData) -> AftPredictiveBundle:
    features = data.inventory.features[:, [0]]
    lower = np.asarray([40, 60, 80, 100, 120, 140, 160, 180] * 2, dtype=np.float64)
    model = train_aft_model(
        features,
        lower,
        lower + 10,
        np.ones(16),
        (data.inventory.context.feature_transform.column_names[0],),
        AftTrainingConfig(AftDistribution.NORMAL, 1.0, rounds=3, maximum_depth=1),
    )
    hashes = {name: _hash(str(name)) for name in range(20)}
    manifest = ModelBundleManifest(
        bundle_id="FULL-normal-scale-1p0",
        acceptance_version="travel-time-v1.2",
        model_schema_version="travel-time-predictive-bundle-v1",
        bundle_kind="FULL",
        aft_distribution="normal",
        aft_scale=1.0,
        model_config_sha256=hashes[0],
        source_lock_sha256=hashes[1],
        normalized_manifest_sha256=hashes[2],
        dataset_manifest_sha256=hashes[3],
        unsampled_manifest_sha256=hashes[4],
        split_manifest_sha256=hashes[5],
        training_row_manifest_sha256=hashes[6],
        validation_row_manifest_sha256=hashes[7],
        calibration_row_manifest_sha256=hashes[8],
        feature_registry_sha256=hashes[9],
        feature_transform_sha256=hashes[10],
        full_feature_order_sha256=hashes[11],
        model_feature_names=model.feature_names,
        model_feature_indices=(0,),
        model_sha256=hashes[12],
        model_wrapper_manifest_sha256=hashes[13],
        calibrator_sha256=hashes[14],
        dependency_lock_sha256=hashes[15],
        code_sha256=hashes[16],
        xgboost_version="3.3.0",
        numpy_version=np.__version__,
        scipy_version="test",
        random_seed=90,
        xgboost_thread_count=1,
        final_test_outcomes_opened=False,
    )
    return AftPredictiveBundle(
        model,
        SigmoidCalibrator(positive_slope=1.0, intercept=0.0),
        manifest,
        full_feature_count=data.inventory.features.shape[1],
    )


def test_final_vectorized_predictions_and_interval_metrics_match_contract() -> None:
    data = _data()
    bundle = _bundle(data)
    prediction = predict_final_bundle(
        bundle,
        data.inventory.features,
        horizons_seconds=(300, 600, 900, 1200, 1800, 2700, 3600),
        quantiles=(0.5, 0.8, 0.9),
        model_horizon_seconds=3600,
    )
    scalar = bundle.predict(data.inventory.features[:2])
    assert np.allclose(prediction.probabilities[:2], [item.probabilities for item in scalar])
    for row_index, item in enumerate(scalar):
        for quantile_index, estimate in enumerate((item.p50, item.p80, item.p90)):
            if estimate.resolved_within_horizon:
                assert (
                    estimate.lower_seconds
                    <= prediction.quantiles_seconds[row_index, quantile_index]
                    <= estimate.upper_seconds
                )
    likelihood = data.likelihood_mask
    filtered = FinalModelPredictions(
        prediction.bundle_id,
        prediction.manifest_sha256,
        prediction.distribution,
        prediction.scale,
        prediction.raw_margins[likelihood],
        prediction.probabilities[likelihood],
        prediction.quantiles_seconds[likelihood],
        prediction.quantiles_resolved[likelihood],
    )
    contributions = calibrated_interval_nll_contributions(
        filtered,
        data.lower_bounds[likelihood],
        data.upper_bounds[likelihood],
        bundle,
    )
    assert np.all(np.isfinite(contributions))
    summary = model_metric_summary(
        data,
        prediction,
        bundle,
        horizons_seconds=(300, 600, 900, 1200, 1800, 2700, 3600),
        quantiles=(0.5, 0.8, 0.9),
    )
    assert summary["interval_negative_log_likelihood"] > 0
    assert len(summary["horizons"]) == 7
    assert len(summary["quantiles"]) == 3
    assert summary["availability"]["all_selected"]["raw_row_count"] == 16


def test_reliability_bounds_slices_and_mass_are_complete() -> None:
    data = _data()
    bundle = _bundle(data)
    prediction = predict_final_bundle(
        bundle,
        data.inventory.features,
        horizons_seconds=(300, 600, 900, 1200, 1800, 2700, 3600),
        quantiles=(0.5, 0.8, 0.9),
        model_horizon_seconds=3600,
    )
    reliability = calibration_table(
        data,
        prediction.probabilities[:, 0],
        horizon_seconds=300,
        initial_bin_count=3,
        minimum_distinct_anchors=1,
    )
    assert reliability["supported"]
    assert reliability["bins"]
    for cell in reliability["bins"]:
        assert cell["population_success_rate_lower"] <= cell["population_success_rate_upper"]
    masses = outcome_mass_report(data)
    assert masses["selected_population"]["RIGHT_CENSORED"]["raw_row_count"] == 2
    assert masses["quarantined_raw_count"] == 2
    identified, success = threshold_status(data.lower_bounds, data.upper_bounds, 150)
    assert identified.sum() == 6
    assert success.sum() == 4
    assert weighted_quantile(np.asarray([1.0, 2.0]), np.asarray([1.0, 3.0]), 0.5) == 2.0


def test_final_metric_contract_rejects_invalid_probabilities_and_bounds() -> None:
    data = _data()
    bundle = _bundle(data)
    prediction = predict_final_bundle(
        bundle,
        data.inventory.features,
        horizons_seconds=(300, 600, 900, 1200, 1800, 2700, 3600),
        quantiles=(0.5, 0.8, 0.9),
        model_horizon_seconds=3600,
    )
    with pytest.raises(ValueError, match="CDF contract"):
        FinalModelPredictions(
            prediction.bundle_id,
            prediction.manifest_sha256,
            prediction.distribution,
            prediction.scale,
            prediction.raw_margins,
            np.full_like(prediction.probabilities, 2.0),
            prediction.quantiles_seconds,
            prediction.quantiles_resolved,
        )
    likelihood = data.likelihood_mask
    with pytest.raises(ValueError, match="bounds"):
        calibrated_interval_nll_contributions(
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
            np.full(likelihood.sum(), -1.0),
            data.upper_bounds[likelihood],
            bundle,
        )


def test_distribution_families_exact_density_and_empty_metrics_are_explicit() -> None:
    data = _data()
    for distribution in ("logistic", "extreme"):
        fake_bundle = SimpleNamespace(
            calibrator=SimpleNamespace(positive_slope=1.0, intercept=0.0),
            manifest=SimpleNamespace(
                aft_distribution=distribution,
                aft_scale=1.0,
                bundle_id=f"FULL-{distribution}",
                manifest_hash=_hash(distribution),
            ),
            raw_margins=lambda features: np.zeros(features.shape[0], dtype=np.float64),
        )
        prediction = predict_final_bundle(
            cast(Any, fake_bundle),
            data.inventory.features[:3],
            horizons_seconds=(300, 600, 900, 1200, 1800, 2700, 3600),
            quantiles=(0.5, 0.8, 0.9),
            model_horizon_seconds=3600,
        )
        values = calibrated_interval_nll_contributions(
            prediction,
            np.asarray([100.0, 0.0, 200.0]),
            np.asarray([100.0, 50.0, math.inf]),
            cast(Any, fake_bundle),
        )
        assert np.all(np.isfinite(values))

    with pytest.raises(ValueError, match="prediction grid"):
        predict_final_bundle(
            cast(Any, fake_bundle),
            data.inventory.features[:1],
            horizons_seconds=(300,),
            quantiles=(0.5, 0.8, 0.9),
            model_horizon_seconds=3600,
        )
    with pytest.raises(ValueError, match="arrays do not align"):
        calibrated_interval_nll_contributions(
            prediction,
            np.asarray([]),
            np.asarray([]),
            cast(Any, fake_bundle),
        )
    with pytest.raises(ValueError, match="weighted quantile"):
        weighted_quantile(np.asarray([]), np.asarray([]), 0.5)
    with pytest.raises(ValueError, match="mask is empty"):
        model_metric_summary(
            data,
            FinalModelPredictions(
                prediction.bundle_id,
                prediction.manifest_sha256,
                prediction.distribution,
                prediction.scale,
                np.zeros(16),
                np.tile(prediction.probabilities[0], (16, 1)),
                np.tile(prediction.quantiles_seconds[0], (16, 1)),
                np.tile(prediction.quantiles_resolved[0], (16, 1)),
            ),
            cast(Any, fake_bundle),
            horizons_seconds=(300, 600, 900, 1200, 1800, 2700, 3600),
            quantiles=(0.5, 0.8, 0.9),
            mask=np.zeros(16, dtype=np.bool_),
        )
    with pytest.raises(ValueError, match="do not align"):
        calibration_table(
            data,
            np.asarray([0.5]),
            horizon_seconds=300,
            initial_bin_count=2,
            minimum_distinct_anchors=1,
        )
    unresolved = replace(
        data,
        lower_bounds=np.full(16, math.nan),
        upper_bounds=np.full(16, math.nan),
    )
    unsupported = calibration_table(
        unresolved,
        np.full(16, 0.5),
        horizon_seconds=300,
        initial_bin_count=2,
        minimum_distinct_anchors=1,
    )
    assert unsupported["supported"] is False
    assert (
        interval_to_payload(BootstrapInterval(1.0, 0.5, 1.5, 2000, 2, 90))["quantile_method"]
        == "linear"
    )
