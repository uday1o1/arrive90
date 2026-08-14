from __future__ import annotations

import hashlib
import json
import math
from dataclasses import replace
from datetime import date
from pathlib import Path

import numpy as np
import pytest
from arrive90_data_contracts.dataset import DatasetSplit
from arrive90_evaluation import model_training
from arrive90_evaluation.model_training import train_model_registry
from arrive90_evaluation.modeling_data import (
    ExampleMetadata,
    ModelingContext,
    ModelingSplit,
)
from arrive90_features.transform import (
    FeatureTransformInput,
    FittedFeatureTransform,
    fit_feature_transform,
)
from arrive90_features.travel_time_registry import TRAVEL_TIME_V1_REGISTRY
from arrive90_models.predictive_bundle import AftPredictiveBundle
from arrive90_outcomes.travel_time_baselines import EmpiricalMidpointQuery
from scipy import sparse  # type: ignore[import-untyped]


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _transform() -> FittedFeatureTransform:
    values: dict[str, str | int | float | bool | None] = {}
    categories = {
        "route_id": "Blue",
        "direction_id": "0",
        "origin_stop_id": "origin",
        "destination_stop_id": "destination",
        "route_pattern_id": "pattern",
    }
    for name, spec in TRAVEL_TIME_V1_REGISTRY.specs.items():
        if spec.value_type == "categorical":
            values[name] = categories[name]
        elif spec.value_type == "boolean":
            values[name] = False
        elif spec.value_type == "integer":
            values[name] = 1
        elif spec.value_type == "float_or_null":
            values[name] = None
        else:
            values[name] = 1.0
    return fit_feature_transform((FeatureTransformInput("training-row", values),))


def _split(split: DatasetSplit, column_count: int, *, rows: int = 240) -> ModelingSplit:
    rng = np.random.default_rng(90 + list(DatasetSplit).index(split))
    dense = rng.normal(size=(rows, column_count)).astype(np.float32)
    dense[np.abs(dense) < 0.75] = 0
    duration = 300 + np.arange(rows) % 600
    lower = duration.astype(np.float64) - 15
    upper = duration.astype(np.float64) + 15
    states = np.full(rows, "INTERVAL_RESOLVED", dtype=object)
    left = np.arange(rows) % 19 == 0
    right = (np.arange(rows) % 23 == 0) & ~left
    lower[left] = 0
    states[left] = "LEFT_CENSORED"
    upper[right] = math.inf
    states[right] = "RIGHT_CENSORED"
    example_ids = tuple(f"{split.value.lower()}-example-{index:03d}" for index in range(rows))
    anchor_ids = tuple(f"{split.value.lower()}-anchor-{index:03d}" for index in range(rows))
    metadata = tuple(
        ExampleMetadata(
            example_id=example_ids[index],
            anchor_id=anchor_ids[index],
            service_date={
                DatasetSplit.TRAINING: date(2024, 1, 1),
                DatasetSplit.MODEL_VALIDATION: date(2024, 8, 1),
                DatasetSplit.CALIBRATION: date(2024, 10, 1),
            }[split],
            query=EmpiricalMidpointQuery(
                anchor_id=anchor_ids[index],
                route_id="Blue",
                direction_id="0",
                origin_stop_id="origin",
                destination_stop_id="destination",
                destination_offset=2,
                day_type="WEEKDAY",
                time_bucket="06:00-09:00",
            ),
            scheduled_remaining_seconds=float(duration[index]),
        )
        for index in range(rows)
    )
    return ModelingSplit(
        split=split,
        features=sparse.csr_matrix(dense),
        lower_bounds=lower,
        upper_bounds=upper,
        analysis_weights=np.ones(rows),
        example_ids=example_ids,
        anchor_ids=anchor_ids,
        outcome_states=tuple(str(value) for value in states),
        metadata=metadata,
        row_manifest_sha256=_hash(split.value),
        service_dates=(metadata[0].service_date.isoformat(),),
    )


def test_complete_training_registry_is_deterministic_and_loadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transform = _transform()
    context = ModelingContext(
        dataset_root=tmp_path / "dataset",
        population_manifest={},
        population_manifest_sha256=_hash("population"),
        unsampled_manifest={},
        unsampled_manifest_sha256=_hash("unsampled"),
        normalized_manifest_sha256=_hash("normalized"),
        source_lock_sha256=_hash("source"),
        feature_transform=transform,
        feature_transform_sha256=_hash("transform"),
        split_manifest_sha256=_hash("split"),
    )
    splits = {
        split: _split(split, len(transform.column_names))
        for split in (
            DatasetSplit.TRAINING,
            DatasetSplit.MODEL_VALIDATION,
            DatasetSplit.CALIBRATION,
        )
    }
    monkeypatch.setattr(model_training, "load_modeling_context", lambda *args, **kwargs: context)
    monkeypatch.setattr(
        model_training, "load_modeling_split", lambda _context, split: splits[split]
    )

    def deterministic_latency(
        _model: object,
        features: sparse.csr_matrix,
        *,
        warmups: int,
        repetitions: int,
    ) -> float:
        del warmups, repetitions
        return float(features.shape[1]) / 1_000_000

    monkeypatch.setattr(model_training, "_latency_seconds", deterministic_latency)
    config = json.loads(Path("configs/models/travel-time-v1.json").read_text())
    config["candidate_grid"]["rounds"] = 2
    config["candidate_grid"]["maximum_depth"] = 1
    config["candidate_grid"]["scales"] = [5.0]
    config["baseline"]["minimum_finite_examples"] = 2
    config["baseline"]["minimum_distinct_anchors"] = 2
    config["qualification"]["latency_repetitions"] = 1
    config["qualification"]["latency_warmup_repetitions"] = 0
    config["qualification"]["latency_sample_rows"] = 32
    config_path = tmp_path / "model-config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    first = train_model_registry(
        config_path=config_path,
        model_root=tmp_path / "models-first",
        runtime_root=tmp_path / "runtime-first",
    )
    second = train_model_registry(
        config_path=config_path,
        model_root=tmp_path / "models-second",
        runtime_root=tmp_path / "runtime-second",
    )
    assert first.registry_index_sha256 == second.registry_index_sha256
    assert first.promoted_manifest_sha256 == second.promoted_manifest_sha256
    assert first.selection_freeze_sha256 == second.selection_freeze_sha256
    comparison = json.loads(first.validation_comparison_path.read_text())
    assert len(comparison["aft_baselines"]) == 2
    assert len(comparison["diagnostic_ablations"]) == 2
    assert comparison["calibration_fit_accessed"] is False
    assert comparison["final_test_outcomes_opened"] is False
    index = json.loads(first.registry_index_path.read_text())
    assert len(index["entries"]) == 7
    assert index["final_test_outcomes_opened"] is False
    directory = first.registry_index_path.parent / "registry" / first.promoted_manifest_sha256
    bundle = AftPredictiveBundle.load(directory, full_feature_count=len(transform.column_names))
    predictions = bundle.predict(splits[DatasetSplit.MODEL_VALIDATION].features[:2])
    assert len(predictions) == 2
    assert all(
        prediction.probabilities == tuple(sorted(prediction.probabilities))
        for prediction in predictions
    )
    assert not bundle._quantile(0.9, 100.0).resolved_within_horizon
    with pytest.raises(ValueError, match="frozen transform"):
        bundle.predict(sparse.csr_matrix(np.ones((1, 1), dtype=np.float32)))
    with pytest.raises(ValueError, match="model features"):
        AftPredictiveBundle(
            bundle.model,
            bundle.calibrator,
            replace(
                bundle.manifest,
                model_feature_names=("changed", *bundle.manifest.model_feature_names[1:]),
            ),
            len(transform.column_names),
        )
    with pytest.raises(ValueError, match="distribution"):
        AftPredictiveBundle(
            bundle.model,
            bundle.calibrator,
            replace(bundle.manifest, aft_distribution="logistic"),
            len(transform.column_names),
        )
    with pytest.raises(ValueError, match="scale"):
        AftPredictiveBundle(
            bundle.model,
            bundle.calibrator,
            replace(bundle.manifest, aft_scale=99),
            len(transform.column_names),
        )
    with pytest.raises(ValueError, match="60 minutes"):
        AftPredictiveBundle(
            bundle.model,
            bundle.calibrator,
            bundle.manifest,
            len(transform.column_names),
            model_horizon_seconds=100,
        )


def test_bundle_feature_resolution_and_empty_output_root_fail_closed(tmp_path: Path) -> None:
    config = json.loads(Path("configs/models/travel-time-v1.json").read_text())
    names = ("scheduled_remaining_seconds", "route_id=Blue", "anchor_speed")
    indices, selected = model_training._bundle_features("SCHEDULE_CALENDAR", names, config)
    assert indices == (0, 1)
    assert selected == names[:2]
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "existing").write_text("immutable", encoding="utf-8")
    try:
        train_model_registry(model_root=occupied)
    except ValueError as error:
        assert "must be empty" in str(error)
    else:
        raise AssertionError("occupied immutable model root was accepted")
