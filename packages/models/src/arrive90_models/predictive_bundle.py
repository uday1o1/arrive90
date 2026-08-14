"""Common calibrated predictive-distribution interface for every AFT bundle."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
from scipy import sparse  # type: ignore[import-untyped]

from arrive90_models.calibration import SigmoidCalibrator
from arrive90_models.distributions import aft_cdf
from arrive90_models.registry import ModelBundleManifest
from arrive90_models.xgb_aft import MatrixLike, TrainedAftModel


@dataclass(frozen=True, slots=True)
class QuantileEstimate:
    probability: float
    lower_seconds: int | None
    upper_seconds: int | None
    resolved_within_horizon: bool


@dataclass(frozen=True, slots=True)
class DistributionPrediction:
    raw_margin: float
    horizons_seconds: tuple[int, ...]
    probabilities: tuple[float, ...]
    p50: QuantileEstimate
    p80: QuantileEstimate
    p90: QuantileEstimate


class PredictiveDistribution(Protocol):
    """One scoring contract shared by full, limited, and intercept-only AFT models."""

    @property
    def metadata(self) -> ModelBundleManifest: ...

    def cdf_matrix(
        self, full_features: MatrixLike, horizons_seconds: tuple[int, ...]
    ) -> np.ndarray: ...

    def predict(self, full_features: MatrixLike) -> tuple[DistributionPrediction, ...]: ...


@dataclass(frozen=True, slots=True)
class AftPredictiveBundle:
    model: TrainedAftModel
    calibrator: SigmoidCalibrator
    manifest: ModelBundleManifest
    full_feature_count: int
    model_horizon_seconds: int = 3_600
    horizons_seconds: tuple[int, ...] = (300, 600, 900, 1200, 1800, 2700, 3600)

    def __post_init__(self) -> None:
        if self.model.feature_names != self.manifest.model_feature_names:
            raise ValueError("bundle model features do not match the registry manifest")
        if self.model.config.distribution.value != self.manifest.aft_distribution:
            raise ValueError("bundle AFT distribution does not match the registry manifest")
        if self.model.config.scale != self.manifest.aft_scale:
            raise ValueError("bundle AFT scale does not match the registry manifest")
        if self.model_horizon_seconds != 3_600:
            raise ValueError("travel-time-v1 model horizon must remain 60 minutes")

    @property
    def metadata(self) -> ModelBundleManifest:
        return self.manifest

    def _model_features(self, full_features: MatrixLike) -> MatrixLike:
        if len(full_features.shape) != 2 or int(full_features.shape[1]) != self.full_feature_count:
            raise ValueError("full feature matrix does not match the frozen transform")
        row_count = int(full_features.shape[0])
        if self.manifest.bundle_kind == "INTERCEPT_ONLY":
            return sparse.csr_matrix(np.ones((row_count, 1), dtype=np.float32))
        indices = list(self.manifest.model_feature_indices)
        return full_features[:, indices]

    def raw_margins(self, full_features: MatrixLike) -> np.ndarray:
        return self.model.predict_raw_margin(self._model_features(full_features))

    def cdf_matrix(
        self, full_features: MatrixLike, horizons_seconds: tuple[int, ...]
    ) -> np.ndarray:
        raw = self.model.evaluate_cdf(self._model_features(full_features), horizons_seconds)
        calibrated = np.empty_like(raw)
        for row_index in range(raw.shape[0]):
            previous = 0.0
            for horizon_index in range(raw.shape[1]):
                probability = self.calibrator.transform(float(raw[row_index, horizon_index]))
                if probability < previous:
                    if previous - probability > 1e-12:
                        raise ValueError("calibrated CDF violates horizon monotonicity")
                    probability = previous
                calibrated[row_index, horizon_index] = probability
                previous = probability
        return calibrated

    def _calibrated_cdf(self, duration_seconds: int, raw_margin: float) -> float:
        probability = aft_cdf(
            duration_seconds,
            raw_margin=raw_margin,
            scale=self.model.config.scale,
            distribution=self.model.config.distribution,
        )
        return self.calibrator.transform(probability)

    def _quantile(self, probability: float, raw_margin: float) -> QuantileEstimate:
        if self._calibrated_cdf(self.model_horizon_seconds, raw_margin) < probability:
            return QuantileEstimate(probability, None, None, False)
        low = 0
        high = self.model_horizon_seconds
        while high - low > 1:
            middle = (low + high) // 2
            if self._calibrated_cdf(middle, raw_margin) >= probability:
                high = middle
            else:
                low = middle
        return QuantileEstimate(probability, low, high, True)

    def predict(self, full_features: MatrixLike) -> tuple[DistributionPrediction, ...]:
        margins = self.raw_margins(full_features)
        probabilities = self.cdf_matrix(full_features, self.horizons_seconds)
        predictions: list[DistributionPrediction] = []
        for row_index, margin in enumerate(margins):
            raw_margin = float(margin)
            predictions.append(
                DistributionPrediction(
                    raw_margin=raw_margin,
                    horizons_seconds=self.horizons_seconds,
                    probabilities=tuple(float(value) for value in probabilities[row_index]),
                    p50=self._quantile(0.5, raw_margin),
                    p80=self._quantile(0.8, raw_margin),
                    p90=self._quantile(0.9, raw_margin),
                )
            )
        return tuple(predictions)

    @classmethod
    def load(cls, bundle_directory: Path, *, full_feature_count: int) -> AftPredictiveBundle:
        payload = json.loads((bundle_directory / "manifest.json").read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("bundle registry manifest must be an object")
        manifest = ModelBundleManifest.from_payload(payload)
        model = TrainedAftModel.load(
            bundle_directory / "model.ubj", bundle_directory / "model-manifest.json"
        )
        calibration_payload = json.loads(
            (bundle_directory / "calibration.json").read_text(encoding="utf-8")
        )
        if not isinstance(calibration_payload, dict) or not isinstance(
            calibration_payload.get("calibrator"), dict
        ):
            raise ValueError("bundle calibration artifact is invalid")
        calibrator = SigmoidCalibrator.from_manifest(calibration_payload["calibrator"])
        return cls(model, calibrator, manifest, full_feature_count)
