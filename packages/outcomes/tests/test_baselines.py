from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from arrive90_data_contracts.candidates import CandidateItinerary, TransitLeg
from arrive90_outcomes.baselines import (
    BaselineContext,
    DelayObservation,
    EmpiricalTimeDistribution,
    PointResidualDistribution,
    RollingMedianDelay,
    ThresholdExample,
    fastest_candidate,
    fit_monotonic_logistic,
    official_prediction_probability,
    static_schedule_probability,
)

NOW = datetime(2025, 1, 1, tzinfo=UTC)


def _candidate(identifier: str, arrival_minutes: int) -> CandidateItinerary:
    return CandidateItinerary(
        (
            TransitLeg(
                identifier,
                "Red",
                0,
                f"trip-{identifier}",
                "a",
                "A",
                "b",
                "B",
                NOW,
                NOW + timedelta(minutes=arrival_minutes),
                ("a", "b"),
            ),
        ),
        (),
    )


def test_deterministic_schedule_prediction_and_fastest_baselines() -> None:
    assert static_schedule_probability(scheduled_arrival_seconds=10, deadline_seconds=10) == 1
    assert static_schedule_probability(scheduled_arrival_seconds=11, deadline_seconds=10) == 0
    assert (
        official_prediction_probability(predicted_arrival_seconds=None, deadline_seconds=10) is None
    )
    assert official_prediction_probability(predicted_arrival_seconds=9, deadline_seconds=10) == 1
    early = _candidate("early", 10)
    late = _candidate("late", 20)
    assert fastest_candidate((late, early)) == early
    with pytest.raises(ValueError, match="at least one"):
        fastest_candidate(())


def test_rolling_median_and_empirical_distribution_are_training_only_summaries() -> None:
    key = ("Red", "0", "A", "weekday", "12:00")
    rolling = RollingMedianDelay.fit(
        (
            DelayObservation(key, 30),
            DelayObservation(key, 10),
            DelayObservation(key, 20),
        )
    )
    assert rolling.predict(key) == 20
    assert rolling.predict(("unknown",)) is None
    empirical = EmpiricalTimeDistribution.fit(((key, 10), (key, 20), (key, 30)))
    assert empirical.cdf(key, 20) == pytest.approx(2 / 3)
    assert empirical.quantile(key, 0.5) == 20
    assert empirical.cdf(("unknown",), 20) is None
    with pytest.raises(ValueError, match="positive"):
        EmpiricalTimeDistribution.fit(((key, 0),))


def test_monotonic_logistic_probability_never_decreases_with_deadline() -> None:
    examples = tuple(
        ThresholdExample(slack, success, 1.0)
        for slack, success in ((5, False), (10, False), (20, True), (30, True))
    )
    model = fit_monotonic_logistic(examples)
    predictions = [model.probability(slack) for slack in range(5, 31)]
    assert predictions == sorted(predictions)
    assert model.nonnegative_slack_coefficient >= 0
    with pytest.raises(ValueError, match="configuration"):
        fit_monotonic_logistic(())


def test_point_residual_distribution_uses_training_residuals() -> None:
    distribution = PointResidualDistribution.fit(((10, 12), (10, 8), (10, 10)))
    assert distribution.cdf(10, 10) == pytest.approx(2 / 3)
    with pytest.raises(ValueError, match="cannot be empty"):
        PointResidualDistribution.fit(())


def test_baseline_evaluation_context_rejects_input_mismatch() -> None:
    context = BaselineContext("candidates", "temporal", "queries", "oracle", "decision")
    context.require_same_evidence(context)
    with pytest.raises(ValueError, match="identical frozen evidence"):
        context.require_same_evidence(replace(context, temporal_view_hash="future"))
