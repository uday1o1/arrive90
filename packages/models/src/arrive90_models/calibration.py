"""Strictly increasing shared sigmoid calibration for CDF probabilities."""

from __future__ import annotations

import math
from dataclasses import dataclass


def _logit(probability: float) -> float:
    return math.log(probability) - math.log1p(-probability)


@dataclass(frozen=True)
class SigmoidCalibrator:
    positive_slope: float
    intercept: float

    def __post_init__(self) -> None:
        if self.positive_slope <= 0:
            raise ValueError("calibrator slope must be strictly positive")

    def transform(self, probability: float) -> float:
        if not 0 <= probability <= 1:
            raise ValueError("calibration input must be inside zero and one")
        if probability in (0.0, 1.0):
            return probability
        margin = self.positive_slope * _logit(probability) + self.intercept
        if margin >= 0:
            return 1.0 / (1.0 + math.exp(-margin))
        exponent = math.exp(margin)
        return exponent / (1.0 + exponent)


@dataclass(frozen=True)
class CalibrationCell:
    probability: float
    success: bool
    weight: float


def fit_sigmoid_calibrator(
    cells: tuple[CalibrationCell, ...],
    *,
    learning_rate: float = 0.01,
    iterations: int = 4_000,
) -> SigmoidCalibrator:
    if not cells or learning_rate <= 0 or iterations <= 0:
        raise ValueError("calibrator fit configuration is invalid")
    if any(not 0 < cell.probability < 1 or cell.weight <= 0 for cell in cells):
        raise ValueError("calibration cells require interior probabilities and positive weights")
    log_slope = 0.0
    intercept = 0.0
    total_weight = sum(cell.weight for cell in cells)
    for _ in range(iterations):
        slope = math.exp(log_slope)
        slope_gradient = 0.0
        intercept_gradient = 0.0
        calibrator = SigmoidCalibrator(slope, intercept)
        for cell in cells:
            prediction = calibrator.transform(cell.probability)
            error = prediction - float(cell.success)
            slope_gradient += cell.weight * error * _logit(cell.probability) * slope
            intercept_gradient += cell.weight * error
        log_slope -= learning_rate * slope_gradient / total_weight
        intercept -= learning_rate * intercept_gradient / total_weight
    return SigmoidCalibrator(math.exp(log_slope), intercept)


def calibrate_grid(
    probabilities: tuple[float, ...], calibrator: SigmoidCalibrator
) -> tuple[float, ...]:
    transformed = tuple(calibrator.transform(value) for value in probabilities)
    if transformed != tuple(sorted(transformed)):
        raise ValueError("calibrator violated CDF ordering")
    return transformed
