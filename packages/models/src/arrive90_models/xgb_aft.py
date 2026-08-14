"""Pinned deterministic XGBoost interval-censored AFT model wrapper."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import xgboost as xgb
from scipy import sparse  # type: ignore[import-untyped]

from arrive90_models.distributions import AftDistribution, aft_cdf

type MatrixLike = np.ndarray | sparse.spmatrix
MODEL_MANIFEST_VERSION = "xgboost-aft-v1"


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


@dataclass(frozen=True, slots=True)
class AftTrainingConfig:
    distribution: AftDistribution
    scale: float
    rounds: int = 40
    maximum_depth: int = 3
    learning_rate: float = 0.05
    seed: int = 90
    nthread: int = 1
    tree_method: str = "hist"
    subsample: float = 1.0

    def __post_init__(self) -> None:
        if (
            self.scale <= 0
            or self.rounds <= 0
            or self.maximum_depth <= 0
            or self.learning_rate <= 0
            or self.nthread != 1
            or self.tree_method != "hist"
            or self.subsample != 1.0
        ):
            raise ValueError("AFT training configuration violates the deterministic contract")

    @property
    def manifest(self) -> dict[str, object]:
        payload = asdict(self)
        payload["distribution"] = self.distribution.value
        return payload

    @classmethod
    def from_manifest(cls, payload: Mapping[str, Any]) -> AftTrainingConfig:
        return cls(
            distribution=AftDistribution(str(payload["distribution"])),
            scale=float(payload["scale"]),
            rounds=int(payload["rounds"]),
            maximum_depth=int(payload["maximum_depth"]),
            learning_rate=float(payload["learning_rate"]),
            seed=int(payload["seed"]),
            nthread=int(payload["nthread"]),
            tree_method=str(payload["tree_method"]),
            subsample=float(payload["subsample"]),
        )


def _matrix_shape(features: MatrixLike) -> tuple[int, int]:
    if len(features.shape) != 2:
        raise ValueError("AFT feature matrix must be two dimensional")
    return int(features.shape[0]), int(features.shape[1])


@dataclass(frozen=True, slots=True)
class TrainedAftModel:
    booster: xgb.Booster
    config: AftTrainingConfig
    feature_names: tuple[str, ...]
    lineage: Mapping[str, str]

    def predict_raw_margin(self, features: MatrixLike) -> np.ndarray:
        row_count, column_count = _matrix_shape(features)
        if column_count != len(self.feature_names):
            raise ValueError("inference feature matrix does not match the frozen feature order")
        if row_count == 0:
            return np.empty(0, dtype=np.float64)
        matrix = xgb.DMatrix(features, feature_names=list(self.feature_names))
        return np.asarray(self.booster.predict(matrix, output_margin=True), dtype=np.float64)

    def evaluate_cdf(self, features: MatrixLike, horizons_seconds: tuple[int, ...]) -> np.ndarray:
        if not horizons_seconds or tuple(sorted(set(horizons_seconds))) != horizons_seconds:
            raise ValueError("AFT horizons must be unique and increasing")
        margins = self.predict_raw_margin(features)
        probabilities = np.empty((len(margins), len(horizons_seconds)), dtype=np.float64)
        for row_index, margin in enumerate(margins):
            for horizon_index, horizon in enumerate(horizons_seconds):
                probabilities[row_index, horizon_index] = aft_cdf(
                    horizon,
                    raw_margin=float(margin),
                    scale=self.config.scale,
                    distribution=self.config.distribution,
                )
        return probabilities

    def save(self, model_path: Path, manifest_path: Path) -> dict[str, Any]:
        """Serialize the model and a complete, content-bound manifest."""

        model_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self.booster.save_model(model_path)
        model_sha256 = _sha256_bytes(model_path.read_bytes())
        feature_order_sha256 = _sha256_bytes(_canonical_json(list(self.feature_names)))
        manifest: dict[str, Any] = {
            "config": self.config.manifest,
            "feature_names": list(self.feature_names),
            "feature_order_sha256": feature_order_sha256,
            "lineage": dict(sorted(self.lineage.items())),
            "manifest_version": MODEL_MANIFEST_VERSION,
            "model_sha256": model_sha256,
            "raw_margin_semantics": "log event time location, not a probability",
            "xgboost_version": xgb.__version__,
        }
        manifest_path.write_bytes(_canonical_json(manifest))
        return manifest

    @classmethod
    def load(cls, model_path: Path, manifest_path: Path) -> TrainedAftModel:
        """Load and verify a serialized AFT model without mutable defaults."""

        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("manifest_version") != MODEL_MANIFEST_VERSION:
            raise ValueError("AFT model manifest version is invalid")
        if raw.get("xgboost_version") != xgb.__version__:
            raise ValueError("AFT model XGBoost version does not match the pinned runtime")
        if raw.get("model_sha256") != _sha256_bytes(model_path.read_bytes()):
            raise ValueError("AFT model bytes do not match the manifest")
        raw_features = raw.get("feature_names")
        raw_lineage = raw.get("lineage")
        raw_config = raw.get("config")
        if (
            not isinstance(raw_features, list)
            or not all(isinstance(value, str) for value in raw_features)
            or not isinstance(raw_lineage, dict)
            or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in raw_lineage.items()
            )
            or not isinstance(raw_config, dict)
        ):
            raise ValueError("AFT model manifest fields are invalid")
        features = tuple(raw_features)
        if raw.get("feature_order_sha256") != _sha256_bytes(_canonical_json(list(features))):
            raise ValueError("AFT model feature order hash is invalid")
        booster = xgb.Booster()
        booster.load_model(model_path)
        return cls(
            booster=booster,
            config=AftTrainingConfig.from_manifest(raw_config),
            feature_names=features,
            lineage={str(key): str(value) for key, value in raw_lineage.items()},
        )


def _validate_bounds(lower_bounds: np.ndarray, upper_bounds: np.ndarray) -> None:
    if np.any(np.isnan(lower_bounds)) or np.any(np.isnan(upper_bounds)):
        raise ValueError("AFT labels cannot contain NaN")
    if np.any(lower_bounds < 0) or np.any(upper_bounds <= 0) or np.any(upper_bounds < lower_bounds):
        raise ValueError("AFT labels must satisfy 0 <= lower <= upper and upper > 0")
    if np.any((lower_bounds == 0) & np.isinf(upper_bounds)):
        raise ValueError("AFT zero-lower rows require a finite positive upper bound")


def train_aft_model(
    features: MatrixLike,
    lower_bounds: np.ndarray,
    upper_bounds: np.ndarray,
    weights: np.ndarray,
    feature_names: tuple[str, ...],
    config: AftTrainingConfig,
    *,
    lineage: Mapping[str, str] | None = None,
) -> TrainedAftModel:
    """Fit one CPU-only deterministic AFT model with ranged labels."""

    row_count, column_count = _matrix_shape(features)
    if column_count != len(feature_names) or len(set(feature_names)) != len(feature_names):
        raise ValueError("feature matrix shape does not match unique feature names")
    if not row_count or any(
        len(values) != row_count for values in (lower_bounds, upper_bounds, weights)
    ):
        raise ValueError("AFT labels and weights must align with nonempty features")
    _validate_bounds(lower_bounds, upper_bounds)
    if np.any(~np.isfinite(weights)) or np.any(weights <= 0):
        raise ValueError("AFT weights must be finite and positive")
    matrix = xgb.DMatrix(features, feature_names=list(feature_names), weight=weights)
    matrix.set_float_info("label_lower_bound", lower_bounds)
    matrix.set_float_info("label_upper_bound", upper_bounds)
    parameters = {
        "aft_loss_distribution": config.distribution.value,
        "aft_loss_distribution_scale": config.scale,
        "colsample_bytree": 1.0,
        "disable_default_eval_metric": False,
        "eta": config.learning_rate,
        "eval_metric": "aft-nloglik",
        "gamma": 0.0,
        "lambda": 1.0,
        "max_depth": config.maximum_depth,
        "min_child_weight": 1.0,
        "nthread": config.nthread,
        "objective": "survival:aft",
        "seed": config.seed,
        "subsample": config.subsample,
        "tree_method": config.tree_method,
    }
    booster = xgb.train(parameters, matrix, num_boost_round=config.rounds)
    return TrainedAftModel(booster, config, feature_names, dict(lineage or {}))
