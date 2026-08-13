"""Pinned deterministic XGBoost interval-censored AFT training wrapper."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import xgboost as xgb

from arrive90_models.distributions import AftDistribution


@dataclass(frozen=True)
class AftTrainingConfig:
    distribution: AftDistribution
    scale: float
    rounds: int = 40
    maximum_depth: int = 3
    learning_rate: float = 0.05
    seed: int = 90

    def __post_init__(self) -> None:
        if self.scale <= 0 or self.rounds <= 0 or self.maximum_depth <= 0:
            raise ValueError("AFT training configuration must be positive")


@dataclass(frozen=True)
class TrainedAftModel:
    booster: xgb.Booster
    config: AftTrainingConfig
    feature_names: tuple[str, ...]

    def predict_raw_margin(self, features: np.ndarray) -> np.ndarray:
        matrix = xgb.DMatrix(features, feature_names=list(self.feature_names))
        return self.booster.predict(matrix, output_margin=True)

    def save(self, model_path: Path, manifest_path: Path) -> None:
        self.booster.save_model(model_path)
        manifest_path.write_text(
            json.dumps(
                {
                    "aft_distribution": self.config.distribution.value,
                    "aft_scale": self.config.scale,
                    "feature_names": self.feature_names,
                    "xgboost_version": xgb.__version__,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )


def train_aft_model(
    features: np.ndarray,
    lower_bounds: np.ndarray,
    upper_bounds: np.ndarray,
    weights: np.ndarray,
    feature_names: tuple[str, ...],
    config: AftTrainingConfig,
) -> TrainedAftModel:
    """Fit one CPU-only deterministic AFT model with exact interval labels."""

    if features.ndim != 2 or features.shape[1] != len(feature_names):
        raise ValueError("feature matrix shape does not match feature names")
    row_count = features.shape[0]
    if not row_count or any(
        len(values) != row_count for values in (lower_bounds, upper_bounds, weights)
    ):
        raise ValueError("AFT labels and weights must align with nonempty features")
    if np.any(lower_bounds <= 0) or np.any(upper_bounds < lower_bounds):
        raise ValueError("AFT labels must satisfy 0 < lower <= upper")
    if np.any(weights <= 0):
        raise ValueError("AFT weights must be positive")
    matrix = xgb.DMatrix(features, feature_names=list(feature_names), weight=weights)
    matrix.set_float_info("label_lower_bound", lower_bounds)
    matrix.set_float_info("label_upper_bound", upper_bounds)
    parameters = {
        "aft_loss_distribution": config.distribution.value,
        "aft_loss_distribution_scale": config.scale,
        "disable_default_eval_metric": False,
        "eta": config.learning_rate,
        "eval_metric": "aft-nloglik",
        "max_depth": config.maximum_depth,
        "nthread": 1,
        "objective": "survival:aft",
        "seed": config.seed,
        "subsample": 1.0,
        "tree_method": "hist",
    }
    booster = xgb.train(parameters, matrix, num_boost_round=config.rounds)
    return TrainedAftModel(booster, config, feature_names)
