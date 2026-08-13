from __future__ import annotations

import numpy as np
import pytest
from arrive90_models.transfer import (
    evaluate_transfer_candidate,
    fit_histogram_boosted_transfer,
    fit_regularized_logistic_transfer,
    select_transfer_candidate,
)
from numpy.typing import NDArray


def _data() -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    features = np.array([[0.0], [1.0], [2.0], [3.0]], dtype=np.float64)
    labels = np.array([0.0, 0.0, 1.0, 1.0], dtype=np.float64)
    weights = np.ones(4, dtype=np.float64)
    return features, labels, weights


def test_transfer_logistic_and_histogram_candidates_are_deterministic() -> None:
    features, labels, weights = _data()
    logistic = fit_regularized_logistic_transfer(
        features, labels, weights, learning_rate=0.01, iterations=500
    )
    logistic_probabilities = logistic.probability(features)
    assert logistic_probabilities[0] < logistic_probabilities[-1]
    first = fit_histogram_boosted_transfer(features, labels, weights, ("buffer",), rounds=3)
    second = fit_histogram_boosted_transfer(features, labels, weights, ("buffer",), rounds=3)
    assert np.array_equal(first.probability(features), second.probability(features))


def test_transfer_selection_rejects_gates_then_uses_frozen_metric_ties() -> None:
    _, labels, weights = _data()
    logistic = evaluate_transfer_candidate(
        "logistic",
        np.array([0.1, 0.2, 0.8, 0.9]),
        labels,
        weights,
        parameter_count=2,
        passes_support=True,
        passes_latency=True,
        passes_slices=True,
    )
    rejected = evaluate_transfer_candidate(
        "boosted",
        np.array([0.01, 0.01, 0.99, 0.99]),
        labels,
        weights,
        parameter_count=10,
        passes_support=False,
        passes_latency=True,
        passes_slices=True,
    )
    assert select_transfer_candidate((rejected, logistic)) == logistic
    assert rejected.rejection_reasons == ("SUPPORT_GATE_FAILED",)
    with pytest.raises(ValueError, match="no transfer"):
        select_transfer_candidate((rejected,))


def test_transfer_fit_and_evaluation_inputs_fail_closed() -> None:
    features, labels, weights = _data()
    with pytest.raises(ValueError, match="binary"):
        fit_regularized_logistic_transfer(features, labels + 2, weights)
    with pytest.raises(ValueError, match="feature shape"):
        fit_histogram_boosted_transfer(features, labels, weights, (), rounds=1)
    with pytest.raises(ValueError, match="not aligned"):
        evaluate_transfer_candidate(
            "bad",
            np.array([0.5]),
            labels,
            weights,
            parameter_count=1,
            passes_support=True,
            passes_latency=True,
            passes_slices=True,
        )
