from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from arrive90_data_contracts.candidates import CandidateItinerary, TransitLeg
from arrive90_decision.contracts import (
    CandidateScore,
    DecisionContext,
    DecisionStatus,
    EligibilityManifest,
    HorizonSupportManifest,
    InitialDecision,
    InitialDecisionRequest,
    QuantileEstimate,
    ScoringState,
)
from arrive90_decision.initial import quantize_probability, select_initial_decision

CUTOFF = datetime(2025, 1, 1, 12, tzinfo=UTC)


def _candidate(index: int, arrival_minutes: int, *, transfer: bool = False) -> CandidateItinerary:
    first_arrival = CUTOFF + timedelta(minutes=arrival_minutes - (5 if transfer else 0))
    first = TransitLeg(
        f"pattern-{index}-a",
        "Red",
        0,
        f"trip-{index}-a",
        "a",
        "station-a",
        "x" if transfer else "b",
        "station-x" if transfer else "station-b",
        CUTOFF + timedelta(minutes=index),
        first_arrival,
        ("a", "x" if transfer else "b"),
    )
    if not transfer:
        return CandidateItinerary((first,), ())
    second = TransitLeg(
        f"pattern-{index}-b",
        "Orange",
        1,
        f"trip-{index}-b",
        "x2",
        "station-x",
        "b",
        "station-b",
        first_arrival + timedelta(minutes=2),
        CUTOFF + timedelta(minutes=arrival_minutes),
        ("x2", "b"),
    )
    return CandidateItinerary((first, second), (60,))


def _score(
    index: int, arrival: int, probability: float, *, transfer: bool = False
) -> CandidateScore:
    return CandidateScore(
        _candidate(index, arrival, transfer=transfer),
        probability,
        f"band-{index}",
        (f"line-{index}", "station-origin", "station-destination"),
        (QuantileEstimate("p90", CUTOFF + timedelta(minutes=arrival + 2), "quantile-p90"),),
    )


def _fixtures(
    scores: tuple[CandidateScore, ...],
    *,
    target: str = "0.90",
    cap_seconds: int = 1_200,
    ready_minutes: int = 0,
) -> tuple[InitialDecisionRequest, DecisionContext, EligibilityManifest, HorizonSupportManifest]:
    cells = {
        cell
        for score in scores
        for cell in (score.prediction_band_cell_id, *score.applicable_slice_cell_ids)
    }
    cells.add("quantile-p90")
    request = InitialDecisionRequest(
        CUTOFF + timedelta(minutes=ready_minutes),
        CUTOFF + timedelta(minutes=ready_minutes + 30),
        Decimal(target),
        cap_seconds,
        "slack-30",
    )
    context = DecisionContext(
        CUTOFF,
        "context-1",
        "ALERT_MASK_V1",
        "manifest",
        tuple((score.itinerary.policy_key, True) for score in scores),
    )
    eligibility = EligibilityManifest(frozenset(cells), frozenset(cells))
    return request, context, eligibility, HorizonSupportManifest(frozenset({"slack-30"}))


def _select(
    scores: tuple[CandidateScore, ...],
    *,
    maximum_extra_time_seconds: int | None = None,
) -> InitialDecision:
    request, context, eligibility, horizon = _fixtures(scores)
    if maximum_extra_time_seconds is not None:
        request = replace(
            request,
            maximum_extra_time_seconds=maximum_extra_time_seconds,
        )
    return select_initial_decision(
        scores,
        request=request,
        context=context,
        eligibility=eligibility,
        horizon_support=horizon,
    )


def test_probability_quantization_is_decimal_half_even() -> None:
    assert quantize_probability(0.9000005) == Decimal("0.900000")
    assert quantize_probability(0.9000015) == Decimal("0.900002")


def test_target_met_prefers_earliest_supported_arrival_and_suppresses_other_slots() -> None:
    fastest = _score(0, 10, 0.80)
    earliest_target = _score(1, 12, 0.91, transfer=True)
    higher_later = _score(2, 14, 0.99)
    result = _select((higher_later, earliest_target, fastest))
    assert result.status is DecisionStatus.TARGET_MET
    assert result.comparator is not None
    assert result.comparator.policy_key == fastest.itinerary.policy_key
    assert result.comparator.deadline_probability is None
    assert result.recommendation is not None
    assert result.recommendation.policy_key == earliest_target.itinerary.policy_key
    assert result.recommendation.deadline_probability == Decimal("0.910000")
    assert result.recommendation.quantile_arrivals[0][0] == "p90"
    assert result.backup_itinerary is not None
    assert result.backup_itinerary.policy_key == higher_later.itinerary.policy_key
    assert result.backup_itinerary.deadline_probability is None
    assert result.explanation_codes == ("EXTRA_TIME_FOR_RELIABILITY",)


def test_cap_is_applied_before_probability_selection() -> None:
    fastest = _score(0, 10, 0.70)
    outside_cap = _score(1, 16, 0.99)
    result = _select(
        (outside_cap, fastest),
        maximum_extra_time_seconds=300,
    )
    assert result.status is DecisionStatus.TARGET_NOT_MET
    assert result.recommendation is not None
    assert result.recommendation.policy_key == fastest.itinerary.policy_key
    assert result.cap_eligible_policy_keys == (fastest.itinerary.policy_key,)


def test_unsupported_target_or_potential_target_fails_closed() -> None:
    low = _score(0, 10, 0.70)
    target = _score(1, 12, 0.92)
    request, context, eligibility, horizon = _fixtures((low, target))
    eligible = eligibility.eligible_cells - {target.prediction_band_cell_id}
    target_declaration = (("0.90", ("target-090",)),)
    known = eligibility.known_cells | {"target-090"}
    unsupported = EligibilityManifest(known, eligible, target_declaration)
    result = select_initial_decision(
        (low, target),
        request=request,
        context=context,
        eligibility=unsupported,
        horizon_support=horizon,
    )
    assert result.status is DecisionStatus.INSUFFICIENT_EVIDENCE
    assert result.recommendation is not None
    assert result.recommendation.policy_key == low.itinerary.policy_key


def test_empty_fallback_returns_schedule_only_comparator() -> None:
    score = _score(0, 10, 0.99)
    request, context, eligibility, horizon = _fixtures((score,))
    unsupported = EligibilityManifest(
        eligibility.known_cells,
        eligibility.eligible_cells - {score.prediction_band_cell_id},
    )
    result = select_initial_decision(
        (score,),
        request=request,
        context=context,
        eligibility=unsupported,
        horizon_support=horizon,
    )
    assert result.status is DecisionStatus.INSUFFICIENT_EVIDENCE
    assert result.trip_start_supported is False
    assert result.recommendation == result.comparator
    assert result.recommendation is not None
    assert result.recommendation.deadline_probability is None


@pytest.mark.parametrize(
    ("ready_minutes", "scoring_state", "status"),
    [
        (16, ScoringState.READY, DecisionStatus.DEGRADED_SCHEDULE_ONLY),
        (0, ScoringState.STALE, DecisionStatus.STALE_LIVE_DATA),
        (0, ScoringState.ABSTAINED, DecisionStatus.MODEL_ABSTAINED),
    ],
)
def test_schedule_only_branches_suppress_model_outputs(
    ready_minutes: int,
    scoring_state: ScoringState,
    status: DecisionStatus,
) -> None:
    score = _score(0, 20, 0.99)
    request, context, eligibility, horizon = _fixtures((score,), ready_minutes=ready_minutes)
    result = select_initial_decision(
        (score,),
        request=request,
        context=context,
        eligibility=eligibility,
        horizon_support=horizon,
        scoring_state=scoring_state,
    )
    assert result.status is status
    assert result.recommendation is not None
    assert result.recommendation.deadline_probability is None
    assert result.trip_start_supported is False


def test_no_eligible_candidate_and_invalid_context() -> None:
    score = _score(0, 10, 0.99)
    request, context, eligibility, horizon = _fixtures((score,))
    empty_context = replace(
        context,
        candidate_eligibility=((score.itinerary.policy_key, False),),
    )
    empty = select_initial_decision(
        (score,),
        request=request,
        context=empty_context,
        eligibility=eligibility,
        horizon_support=horizon,
    )
    assert empty.status is DecisionStatus.NO_SUPPORTED_ITINERARY
    assert empty.comparator is None
    with pytest.raises(ValueError, match="classify every"):
        select_initial_decision(
            (score,),
            request=request,
            context=replace(context, candidate_eligibility=()),
            eligibility=eligibility,
            horizon_support=horizon,
        )


def test_unknown_quantile_is_independently_suppressed_and_payload_is_stable() -> None:
    score = _score(0, 10, 0.99)
    request, context, eligibility, horizon = _fixtures((score,))
    without_quantile = EligibilityManifest(
        eligibility.known_cells,
        eligibility.eligible_cells - {"quantile-p90"},
    )
    result = select_initial_decision(
        (score,),
        request=request,
        context=context,
        eligibility=without_quantile,
        horizon_support=horizon,
    )
    assert result.recommendation is not None
    assert result.recommendation.quantile_arrivals == ()
    assert result.canonical_payload() == result.canonical_payload()


def test_contract_validation_rejects_ambiguous_support_and_scores() -> None:
    score = _score(0, 10, 0.99)
    with pytest.raises(ValueError, match="inside zero and one"):
        replace(score, calibrated_deadline_probability=1.1)
    with pytest.raises(ValueError, match="unique"):
        EligibilityManifest(frozenset(), frozenset(), (("0.90", ()), ("0.90", ())))
    with pytest.raises(ValueError, match="present"):
        EligibilityManifest(frozenset(), frozenset({"unknown"}))
    request, context, eligibility, horizon = _fixtures((score,))
    with pytest.raises(ValueError, match="unique policy"):
        select_initial_decision(
            (score, score),
            request=request,
            context=context,
            eligibility=eligibility,
            horizon_support=horizon,
        )
