"""Exact initial-selection policy from BUILD_PLAN Section 18."""

from __future__ import annotations

from datetime import timedelta
from decimal import ROUND_HALF_EVEN, Decimal

from arrive90_data_contracts.candidates import CandidateItinerary

from arrive90_decision.contracts import (
    CandidateScore,
    DecisionContext,
    DecisionStatus,
    EligibilityManifest,
    HorizonSupportManifest,
    InitialDecision,
    InitialDecisionRequest,
    ScoringState,
    SelectedItinerary,
)

_SIX_PLACES = Decimal("0.000001")


def quantize_probability(probability: float) -> Decimal:
    return Decimal(str(probability)).quantize(_SIX_PLACES, rounding=ROUND_HALF_EVEN)


def _planned_seconds(candidate: CandidateItinerary, request: InitialDecisionRequest) -> int:
    return int((candidate.scheduled_arrival_utc - request.ready_at_utc).total_seconds())


def _schedule_tuple(
    candidate: CandidateItinerary, request: InitialDecisionRequest
) -> tuple[object, ...]:
    return (
        candidate.scheduled_arrival_utc,
        _planned_seconds(candidate, request),
        candidate.scheduled_departure_utc,
        candidate.transfer_count,
        candidate.route_pattern_tuple,
        candidate.platform_stop_tuple,
        candidate.policy_key.encode(),
    )


def _selected(
    score: CandidateScore,
    request: InitialDecisionRequest,
    comparator_planned: int,
    eligibility: EligibilityManifest,
    *,
    expose_model: bool,
) -> SelectedItinerary:
    planned = _planned_seconds(score.itinerary, request)
    quantiles = tuple(
        (item.level, item.arrival_utc)
        for item in score.quantiles
        if expose_model and eligibility.cell_is_eligible(item.support_cell_id)
    )
    return SelectedItinerary(
        policy_key=score.itinerary.policy_key,
        planned_time_seconds=planned,
        extra_planned_time_seconds=planned - comparator_planned,
        deadline_probability=(
            quantize_probability(score.calibrated_deadline_probability) if expose_model else None
        ),
        diagnostic_probability=(score.calibrated_deadline_probability if expose_model else None),
        quantile_arrivals=quantiles,
        model_output_status=(
            "SUPPORTED_SELECTED_OUTPUT" if expose_model else "NOT_SELECTED_OUTPUT_UNVALIDATED"
        ),
    )


def _horizon_supported(
    request: InitialDecisionRequest,
    context: DecisionContext,
    support: HorizonSupportManifest,
) -> bool:
    lead = request.ready_at_utc - context.decision_cutoff_utc
    slack = request.effective_deadline_at_utc - request.ready_at_utc
    return (
        timedelta(0) <= lead <= timedelta(minutes=15)
        and context.decision_cutoff_utc <= request.ready_at_utc
        and timedelta(minutes=5) <= slack <= timedelta(minutes=180)
        and int(slack.total_seconds()) % 300 == 0
        and support.supports(request.deadline_slack_region_id)
    )


def _candidate_supported(score: CandidateScore, eligibility: EligibilityManifest) -> bool:
    cells = (score.prediction_band_cell_id, *score.applicable_slice_cell_ids)
    return all(eligibility.cell_is_eligible(cell_id) for cell_id in cells)


def _requested_supported(
    score: CandidateScore,
    request: InitialDecisionRequest,
    context: DecisionContext,
    eligibility: EligibilityManifest,
    support: HorizonSupportManifest,
) -> bool:
    return (
        _horizon_supported(request, context, support)
        and eligibility.declared_target_is_supported(request.reliability_target)
        and _candidate_supported(score, eligibility)
    )


def select_initial_decision(
    scores: tuple[CandidateScore, ...],
    *,
    request: InitialDecisionRequest,
    context: DecisionContext,
    eligibility: EligibilityManifest,
    horizon_support: HorizonSupportManifest,
    scoring_state: ScoringState = ScoringState.READY,
) -> InitialDecision:
    """Select once from immutable scores and one causally frozen eligibility mask."""

    keys = tuple(score.itinerary.policy_key for score in scores)
    if len(set(keys)) != len(keys):
        raise ValueError("candidate scores must have unique policy keys")
    mask = context.mask
    if any(key not in mask for key in keys):
        raise ValueError("decision context must classify every candidate")
    eligible = tuple(score for score in scores if mask[score.itinerary.policy_key])
    if not eligible:
        return InitialDecision(
            DecisionStatus.NO_SUPPORTED_ITINERARY, None, (), None, None, (), False
        )

    comparator_score = min(eligible, key=lambda score: _schedule_tuple(score.itinerary, request))
    comparator_planned = _planned_seconds(comparator_score.itinerary, request)
    comparator = _selected(
        comparator_score,
        request,
        comparator_planned,
        eligibility,
        expose_model=False,
    )
    lead = request.ready_at_utc - context.decision_cutoff_utc
    schedule_status: DecisionStatus | None = None
    if lead > timedelta(minutes=15):
        schedule_status = DecisionStatus.DEGRADED_SCHEDULE_ONLY
    elif scoring_state is ScoringState.STALE:
        schedule_status = DecisionStatus.STALE_LIVE_DATA
    elif scoring_state is ScoringState.ABSTAINED:
        schedule_status = DecisionStatus.MODEL_ABSTAINED
    if schedule_status is not None:
        return InitialDecision(
            schedule_status,
            comparator,
            (comparator.policy_key,),
            comparator,
            None,
            (),
            False,
        )

    cap_scores = tuple(
        score
        for score in eligible
        if _planned_seconds(score.itinerary, request) - comparator_planned
        <= request.maximum_extra_time_seconds
    )
    cap_keys = tuple(
        score.itinerary.policy_key
        for score in sorted(cap_scores, key=lambda item: _schedule_tuple(item.itinerary, request))
    )
    target = request.reliability_target
    target_qualified = tuple(
        score
        for score in cap_scores
        if quantize_probability(score.calibrated_deadline_probability) >= target
        and _requested_supported(score, request, context, eligibility, horizon_support)
    )
    if target_qualified:
        rank = lambda score: (  # noqa: E731
            score.itinerary.scheduled_arrival_utc,
            -quantize_probability(score.calibrated_deadline_probability),
            _planned_seconds(score.itinerary, request),
            score.itinerary.transfer_count,
            score.itinerary.route_pattern_tuple,
            score.itinerary.platform_stop_tuple,
            score.itinerary.policy_key.encode(),
        )
        ordered = sorted(target_qualified, key=rank)
        selected_score = ordered[0]
        backup_score = ordered[1] if len(ordered) > 1 else None
        status = DecisionStatus.TARGET_MET
    else:
        fallback_supported = tuple(
            score
            for score in cap_scores
            if _horizon_supported(request, context, horizon_support)
            and _candidate_supported(score, eligibility)
        )
        if not fallback_supported:
            return InitialDecision(
                DecisionStatus.INSUFFICIENT_EVIDENCE,
                comparator,
                cap_keys,
                comparator,
                None,
                ("HISTORICAL_SUPPORT_SPARSE",),
                False,
            )
        rank = lambda score: (  # noqa: E731
            -quantize_probability(score.calibrated_deadline_probability),
            score.itinerary.scheduled_arrival_utc,
            _planned_seconds(score.itinerary, request),
            score.itinerary.transfer_count,
            score.itinerary.route_pattern_tuple,
            score.itinerary.platform_stop_tuple,
            score.itinerary.policy_key.encode(),
        )
        ordered = sorted(fallback_supported, key=rank)
        selected_score = ordered[0]
        backup_score = ordered[1] if len(ordered) > 1 else None
        every_potential_target_supported = all(
            _requested_supported(score, request, context, eligibility, horizon_support)
            for score in cap_scores
            if quantize_probability(score.calibrated_deadline_probability) >= target
        )
        status = (
            DecisionStatus.TARGET_NOT_MET
            if eligibility.declared_target_is_supported(target) and every_potential_target_supported
            else DecisionStatus.INSUFFICIENT_EVIDENCE
        )

    recommendation = _selected(
        selected_score,
        request,
        comparator_planned,
        eligibility,
        expose_model=True,
    )
    backup = (
        _selected(
            backup_score,
            request,
            comparator_planned,
            eligibility,
            expose_model=False,
        )
        if backup_score is not None
        else None
    )
    explanations = (
        ("EXTRA_TIME_FOR_RELIABILITY",)
        if recommendation.extra_planned_time_seconds
        and recommendation.extra_planned_time_seconds > 0
        else ()
    )
    return InitialDecision(status, comparator, cap_keys, recommendation, backup, explanations, True)
