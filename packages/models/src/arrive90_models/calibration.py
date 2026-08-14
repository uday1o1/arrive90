"""Frozen positive-slope logistic calibration for AFT CDF probabilities."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.optimize import minimize  # type: ignore[import-untyped]

CALIBRATOR_VERSION = "positive-slope-logistic-v1"
SLOPE_EPSILON = 1e-6


def _logit(probability: float) -> float:
    return math.log(probability) - math.log1p(-probability)


def _sigmoid(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exponent = math.exp(value)
    return exponent / (1.0 + exponent)


def _softplus(value: float) -> float:
    if value > 0:
        return value + math.log1p(math.exp(-value))
    return math.log1p(math.exp(value))


@dataclass(frozen=True, slots=True)
class SigmoidCalibrator:
    positive_slope: float
    intercept: float
    optimizer_alpha: float | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.positive_slope) or self.positive_slope <= 0:
            raise ValueError("calibrator slope must be finite and strictly positive")
        if not math.isfinite(self.intercept):
            raise ValueError("calibrator intercept must be finite")

    @property
    def manifest(self) -> dict[str, Any]:
        return {
            "family": CALIBRATOR_VERSION,
            "intercept": self.intercept,
            "optimizer_alpha": self.optimizer_alpha,
            "positive_slope": self.positive_slope,
            "slope_epsilon": SLOPE_EPSILON,
        }

    @classmethod
    def from_manifest(cls, payload: dict[str, Any]) -> SigmoidCalibrator:
        if payload.get("family") != CALIBRATOR_VERSION:
            raise ValueError("calibrator family is invalid")
        alpha = payload.get("optimizer_alpha")
        return cls(
            positive_slope=float(payload["positive_slope"]),
            intercept=float(payload["intercept"]),
            optimizer_alpha=None if alpha is None else float(alpha),
        )

    def transform(self, probability: float) -> float:
        if not 0 <= probability <= 1:
            raise ValueError("calibration input must be inside zero and one")
        if probability in (0.0, 1.0):
            return probability
        return _sigmoid(self.positive_slope * _logit(probability) + self.intercept)


@dataclass(frozen=True, slots=True)
class CalibrationCell:
    probability: float
    success: bool
    weight: float


def fit_sigmoid_calibrator(
    cells: tuple[CalibrationCell, ...],
    *,
    maximum_iterations: int = 1_000,
    ftol: float = 1e-12,
    gtol: float = 1e-8,
) -> SigmoidCalibrator:
    """Fit the frozen SciPy L-BFGS-B calibration protocol in float64."""

    if not cells or maximum_iterations <= 0 or ftol <= 0 or gtol <= 0:
        raise ValueError("calibrator fit configuration is invalid")
    if any(
        not 0 <= cell.probability <= 1 or not math.isfinite(cell.weight) or cell.weight <= 0
        for cell in cells
    ):
        raise ValueError("calibration cells require bounded probabilities and positive weights")
    interior = tuple(cell for cell in cells if 0 < cell.probability < 1)
    if not interior:
        raise ValueError("calibration fit requires at least one interior probability")
    logits = np.asarray([_logit(cell.probability) for cell in interior], dtype=np.float64)
    outcomes = np.asarray([float(cell.success) for cell in interior], dtype=np.float64)
    weights = np.asarray([cell.weight for cell in interior], dtype=np.float64)
    total_weight = float(np.sum(weights))

    def objective(parameters: np.ndarray) -> tuple[float, np.ndarray]:
        alpha = float(parameters[0])
        intercept = float(parameters[1])
        slope = _softplus(alpha) + SLOPE_EPSILON
        margins = slope * logits + intercept
        losses = np.logaddexp(0.0, margins) - outcomes * margins
        probabilities = np.empty_like(margins)
        nonnegative = margins >= 0
        probabilities[nonnegative] = 1.0 / (1.0 + np.exp(-margins[nonnegative]))
        exponent = np.exp(margins[~nonnegative])
        probabilities[~nonnegative] = exponent / (1.0 + exponent)
        errors = probabilities - outcomes
        slope_derivative = _sigmoid(alpha)
        gradient = np.asarray(
            [
                np.sum(weights * errors * logits) * slope_derivative / total_weight,
                np.sum(weights * errors) / total_weight,
            ],
            dtype=np.float64,
        )
        return float(np.sum(weights * losses) / total_weight), gradient

    result = minimize(
        objective,
        np.asarray([0.0, 0.0], dtype=np.float64),
        method="L-BFGS-B",
        jac=True,
        options={"ftol": ftol, "gtol": gtol, "maxiter": maximum_iterations},
    )
    if not result.success or not np.all(np.isfinite(result.x)):
        raise ValueError(f"calibrator optimization failed: {result.message}")
    alpha = float(result.x[0])
    return SigmoidCalibrator(
        positive_slope=_softplus(alpha) + SLOPE_EPSILON,
        intercept=float(result.x[1]),
        optimizer_alpha=alpha,
    )


def calibrate_grid(
    probabilities: tuple[float, ...], calibrator: SigmoidCalibrator
) -> tuple[float, ...]:
    transformed = tuple(calibrator.transform(value) for value in probabilities)
    if transformed != tuple(sorted(transformed)):
        raise ValueError("calibrator violated CDF ordering")
    return transformed
