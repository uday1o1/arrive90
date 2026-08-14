from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast

from arrive90_evaluation.final_artifacts import (
    LoadedModelRegistry,
    load_prediction_artifact,
    write_prediction_artifact,
)
from arrive90_evaluation.final_metrics import FinalModelPredictions, predict_final_bundle
from arrive90_evaluation.final_report import (
    build_claim_registry,
    build_final_report,
    data_from_prediction_table,
    predictions_from_table,
)
from arrive90_outcomes.travel_time_baselines import EmpiricalMidpointBaseline
from test_final_metrics import _bundle, _data, _hash


def test_final_report_rebuilds_all_tables_and_claims_from_predictions(tmp_path: Path) -> None:
    data = _data()
    bundle = _bundle(data)
    base = predict_final_bundle(
        bundle,
        data.inventory.features,
        horizons_seconds=(300, 600, 900, 1200, 1800, 2700, 3600),
        quantiles=(0.5, 0.8, 0.9),
        model_horizon_seconds=3600,
    )
    model_order = (
        "FULL-extreme-scale-1p0",
        "FULL-logistic-scale-1p0",
        "FULL-normal-scale-0p5",
        "INTERCEPT_ONLY-normal",
        "NO_POSITION_OBSERVATION-normal",
        "NO_PREFIX_HISTORY-normal",
        "SCHEDULE_CALENDAR-normal",
    )
    predictions = {
        bundle_id: FinalModelPredictions(
            bundle_id,
            _hash(bundle_id),
            base.distribution,
            base.scale,
            base.raw_margins,
            base.probabilities,
            base.quantiles_seconds,
            base.quantiles_resolved,
        )
        for bundle_id in model_order
    }
    empirical = EmpiricalMidpointBaseline(
        ((("GLOBAL_DESTINATION_OFFSET", "1"), 110.0, 16, 16),),
        minimum_finite_examples=1,
        minimum_distinct_anchors=1,
    )
    artifact = write_prediction_artifact(
        tmp_path,
        data,
        predictions,
        empirical,
        protocol_sha256=data.access.protocol_sha256,
        replay_selection_sha256=data.access.replay_selection_sha256,
        model_order=model_order,
    )
    manifest, table = load_prediction_artifact(artifact.manifest_path)
    rebuilt_data = data_from_prediction_table(data.inventory.context, manifest, table)
    rebuilt_predictions = predictions_from_table(manifest, table)
    assert rebuilt_data.outcome_states == data.outcome_states
    assert tuple(rebuilt_predictions) == model_order
    registry = cast(
        LoadedModelRegistry,
        SimpleNamespace(
            bundles=dict.fromkeys(model_order, bundle),
            promoted=bundle,
            promoted_bundle_id="FULL-normal-scale-0p5",
        ),
    )
    config = {
        "bootstrap": {
            "confidence_level": 0.95,
            "numpy_bit_generator": "PCG64",
            "quantile_method": "linear",
            "replicates": 2000,
            "seed": 90,
            "unit": "COMPLETE_SERVICE_DATE",
        },
        "calibration": {
            "initial_equal_analysis_weight_bins": 3,
            "minimum_distinct_anchors_per_bin": 1,
        },
        "failure_case_count": 2,
        "final_test": {
            "start_date": "2024-11-01",
            "end_date": "2024-12-31",
        },
        "horizons_seconds": [300, 600, 900, 1200, 1800, 2700, 3600],
        "quantiles": [0.5, 0.8, 0.9],
        "slice_dimensions": ["day_type", "destination_class", "outcome_class"],
    }
    protocol = {"acceptance_version": "travel-time-v1.2", "version": "freeze"}
    report = build_final_report(
        data.inventory.context,
        config,
        registry,
        manifest,
        table,
        protocol=protocol,
        demo_artifacts={"fixture_sha256": _hash("fixture")},
    )
    assert report["bootstrap"]["replicates"] == 2000
    assert len(report["models"]) == 7
    assert len(report["calibration"]) == 7
    assert report["availability"]["selected_population"]["NO_FOLLOW_UP"]["raw_row_count"] == 2
    assert set(report["slice_tables"]) == {
        "day_type",
        "destination_class",
        "outcome_class",
    }
    assert len(report["failure_cases"]) == 2
    claims = build_claim_registry(report, _hash("report"))
    assert len(claims["claims"]) == 5
    assert all(claim["artifact_sha256"] == _hash("report") for claim in claims["claims"])
