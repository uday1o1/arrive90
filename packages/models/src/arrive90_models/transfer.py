"""Deterministic transfer classifier candidates and pre-test selection."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import xgboost as xgb
from numpy.typing import NDArray


def _sigmoid(margins: NDArray[np.float64]) -> NDArray[np.float64]:
    output = np.empty_like(margins)
    positive = margins >= 0
    output[positive] = 1 / (1 + np.exp(-margins[positive]))
    exponent = np.exp(margins[~positive])
    output[~positive] = exponent / (1 + exponent)
    return output


@dataclass(frozen=True)
class RegularizedLogisticTransfer:
    coefficients: NDArray[np.float64]
    intercept: float

    def probability(self, features: NDArray[np.float64]) -> NDArray[np.float64]:
        return _sigmoid(features @ self.coefficients + self.intercept)


def fit_regularized_logistic_transfer(
    features: NDArray[np.float64],
    labels: NDArray[np.float64],
    weights: NDArray[np.float64],
    *,
    l2: float = 0.01,
    learning_rate: float = 0.05,
    iterations: int = 2_000,
) -> RegularizedLogisticTransfer:
    if features.ndim != 2 or len(features) == 0 or labels.shape != (len(features),):
        raise ValueError("transfer logistic inputs have incompatible shapes")
    if weights.shape != labels.shape or np.any(weights <= 0):
        raise ValueError("transfer logistic weights must be positive and aligned")
    if np.any((labels != 0) & (labels != 1)):
        raise ValueError("transfer logistic labels must be binary")
    if l2 < 0 or learning_rate <= 0 or iterations <= 0:
        raise ValueError("transfer logistic configuration is invalid")
    coefficients = np.zeros(features.shape[1], dtype=np.float64)
    intercept = 0.0
    total_weight = float(np.sum(weights))
    for _ in range(iterations):
        errors = _sigmoid(features @ coefficients + intercept) - labels
        coefficients -= learning_rate * (
            features.T @ (weights * errors) / total_weight + l2 * coefficients
        )
        intercept -= learning_rate * float(np.sum(weights * errors)) / total_weight
    return RegularizedLogisticTransfer(coefficients, intercept)


@dataclass(frozen=True)
class HistogramBoostedTransfer:
    booster: xgb.Booster
    feature_names: tuple[str, ...]

    def probability(self, features: NDArray[np.float64]) -> NDArray[np.float64]:
        values = self.booster.predict(xgb.DMatrix(features, feature_names=list(self.feature_names)))
        return np.asarray(values, dtype=np.float64)


def fit_histogram_boosted_transfer(
    features: NDArray[np.float64],
    labels: NDArray[np.float64],
    weights: NDArray[np.float64],
    feature_names: tuple[str, ...],
    *,
    rounds: int = 20,
    seed: int = 90,
) -> HistogramBoostedTransfer:
    if features.ndim != 2 or features.shape[1] != len(feature_names) or not len(features):
        raise ValueError("transfer boosted feature shape does not match feature names")
    if labels.shape != (len(features),) or weights.shape != labels.shape:
        raise ValueError("transfer boosted labels and weights are not aligned")
    if np.any((labels != 0) & (labels != 1)) or np.any(weights <= 0) or rounds <= 0:
        raise ValueError("transfer boosted labels, weights, or rounds are invalid")
    matrix = xgb.DMatrix(
        features,
        label=labels,
        weight=weights,
        feature_names=list(feature_names),
    )
    booster = xgb.train(
        {
            "eta": 0.05,
            "eval_metric": "logloss",
            "max_depth": 3,
            "nthread": 1,
            "objective": "binary:logistic",
            "seed": seed,
            "subsample": 1.0,
            "tree_method": "hist",
        },
        matrix,
        num_boost_round=rounds,
    )
    return HistogramBoostedTransfer(booster, feature_names)


@dataclass(frozen=True)
class TransferCandidateEvaluation:
    identifier: str
    eligible: bool
    weighted_log_loss: float
    weighted_brier_score: float
    parameter_count: int
    rejection_reasons: tuple[str, ...] = ()


def evaluate_transfer_candidate(
    identifier: str,
    probabilities: NDArray[np.float64],
    labels: NDArray[np.float64],
    weights: NDArray[np.float64],
    *,
    parameter_count: int,
    passes_support: bool,
    passes_latency: bool,
    passes_slices: bool,
) -> TransferCandidateEvaluation:
    if probabilities.shape != labels.shape or weights.shape != labels.shape or len(labels) == 0:
        raise ValueError("transfer evaluation arrays are not aligned")
    if np.any((probabilities < 0) | (probabilities > 1)) or np.any(weights <= 0):
        raise ValueError("transfer evaluation probabilities or weights are invalid")
    clipped = np.clip(probabilities, 1e-15, 1 - 1e-15)
    total = float(np.sum(weights))
    log_loss = (
        -float(np.sum(weights * (labels * np.log(clipped) + (1 - labels) * np.log1p(-clipped))))
        / total
    )
    brier = float(np.sum(weights * np.square(probabilities - labels))) / total
    reasons: list[str] = []
    if not passes_support:
        reasons.append("SUPPORT_GATE_FAILED")
    if not passes_latency:
        reasons.append("LATENCY_GATE_FAILED")
    if not passes_slices:
        reasons.append("SLICE_GATE_FAILED")
    return TransferCandidateEvaluation(
        identifier,
        not reasons,
        log_loss,
        brier,
        parameter_count,
        tuple(reasons),
    )


def select_transfer_candidate(
    evaluations: tuple[TransferCandidateEvaluation, ...],
) -> TransferCandidateEvaluation:
    eligible = [evaluation for evaluation in evaluations if evaluation.eligible]
    if not eligible:
        raise ValueError("no transfer classifier candidate passed pre-test gates")
    return min(
        eligible,
        key=lambda evaluation: (
            evaluation.weighted_log_loss,
            evaluation.weighted_brier_score,
            evaluation.parameter_count,
            evaluation.identifier.encode(),
        ),
    )
