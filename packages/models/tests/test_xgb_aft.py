from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest
import xgboost as xgb
from arrive90_models.distributions import AftDistribution, aft_cdf
from arrive90_models.xgb_aft import AftTrainingConfig, TrainedAftModel, train_aft_model


@pytest.mark.parametrize("distribution", tuple(AftDistribution))
def test_pinned_xgboost_raw_margin_matches_default_event_time_and_cdf_formula(
    tmp_path: Path, distribution: AftDistribution
) -> None:
    features = np.array([[0.0], [1.0], [2.0], [3.0]], dtype=np.float64)
    lower = np.array([0.0, 120.0, 180.0, 240.0], dtype=np.float64)
    upper = np.array([90.0, 150.0, 210.0, math.inf], dtype=np.float64)
    weights = np.ones(4, dtype=np.float64)
    config = AftTrainingConfig(distribution, 1.0, rounds=3, maximum_depth=1)
    model = train_aft_model(features, lower, upper, weights, ("feature",), config)
    margins = model.predict_raw_margin(features)
    default_prediction = model.booster.predict(xgb.DMatrix(features, feature_names=["feature"]))
    assert np.allclose(np.exp(margins), default_prediction)
    probabilities = [
        aft_cdf(180, raw_margin=float(margin), scale=1, distribution=distribution)
        for margin in margins
    ]
    assert all(0 <= value <= 1 for value in probabilities)
    model_path = tmp_path / "model.ubj"
    manifest_path = tmp_path / "manifest.json"
    model.save(model_path, manifest_path)
    assert model_path.is_file()
    manifest = json.loads(manifest_path.read_text())
    assert manifest["xgboost_version"] == "3.3.0"
    assert manifest["raw_margin_semantics"] == "log event time location, not a probability"
    loaded = TrainedAftModel.load(model_path, manifest_path)
    assert np.allclose(loaded.predict_raw_margin(features), margins)
    assert np.allclose(
        loaded.evaluate_cdf(features, (60, 180)), model.evaluate_cdf(features, (60, 180))
    )


def test_aft_training_rejects_invalid_shape_bounds_and_weights() -> None:
    features = np.array([[1.0]], dtype=np.float64)
    values = np.array([1.0], dtype=np.float64)
    config = AftTrainingConfig(AftDistribution.NORMAL, 1, rounds=1)
    with pytest.raises(ValueError, match="shape"):
        train_aft_model(features, values, values, values, (), config)
    train_aft_model(features, np.array([0.0]), values, values, ("x",), config)
    with pytest.raises(ValueError, match="0 <= lower"):
        train_aft_model(features, np.array([-1.0]), values, values, ("x",), config)
    with pytest.raises(ValueError, match="zero-lower"):
        train_aft_model(
            features,
            np.array([0.0]),
            np.array([math.inf]),
            values,
            ("x",),
            config,
        )
    with pytest.raises(ValueError, match="weights"):
        train_aft_model(features, values, values, np.array([0.0]), ("x",), config)


def test_aft_config_inference_and_manifest_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="deterministic contract"):
        AftTrainingConfig(AftDistribution.NORMAL, 1, nthread=2)
    features = np.array([[1.0], [2.0]], dtype=np.float64)
    values = np.array([60.0, 120.0], dtype=np.float64)
    model = train_aft_model(
        features,
        values,
        values,
        np.ones(2),
        ("x",),
        AftTrainingConfig(AftDistribution.NORMAL, 1, rounds=1),
    )
    with pytest.raises(ValueError, match="frozen feature order"):
        model.predict_raw_margin(np.ones((1, 2)))
    assert model.predict_raw_margin(np.empty((0, 1))).size == 0
    with pytest.raises(ValueError, match="unique and increasing"):
        model.evaluate_cdf(features, (60, 60))
    model_path = tmp_path / "model.ubj"
    manifest_path = tmp_path / "manifest.json"
    manifest = model.save(model_path, manifest_path)
    manifest["manifest_version"] = "future"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="version"):
        TrainedAftModel.load(model_path, manifest_path)
    model.save(model_path, manifest_path)
    model_path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="model bytes"):
        TrainedAftModel.load(model_path, manifest_path)
