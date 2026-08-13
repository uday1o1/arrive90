"""Deterministic recovery policy and explicit trip-state transition graph."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from arrive90_data_contracts.candidates import CandidateItinerary

from arrive90_decision.contracts import (
    DecisionContext,
    RecoveryDecision,
    RecoveryReason,
    RecoveryStatus,
    RecoveryTriggerInput,
    SelectedItinerary,
    TripState,
)


def recovery_reasons(trigger: RecoveryTriggerInput) -> tuple[RecoveryReason, ...]:
    if trigger.state is not TripState.AT_TRANSFER:
        return ()
    reasons: list[RecoveryReason] = []
    if trigger.closure_applies:
        reasons.append(RecoveryReason.CAUSAL_CLOSURE)
    supported_low_probability = (
        trigger.original_confirmatory_policy
        and not trigger.recovery_ever_activated
        and trigger.rounded_transfer_probability is not None
        and trigger.rounded_transfer_probability < Decimal("0.500000")
        and trigger.candidate_decile_supported
        and trigger.station_supported
        and trigger.selected_policy_decile_supported
    )
    if supported_low_probability:
        reasons.append(RecoveryReason.LOW_TRANSFER_PROBABILITY)
    return tuple(reasons)


def _planned(candidate: CandidateItinerary, ready_at: datetime) -> int:
    return int((candidate.scheduled_arrival_utc - ready_at).total_seconds())


def _rank(candidate: CandidateItinerary, ready_at: datetime) -> tuple[object, ...]:
    return (
        candidate.scheduled_arrival_utc,
        _planned(candidate, ready_at),
        candidate.scheduled_departure_utc,
        candidate.transfer_count,
        candidate.route_pattern_tuple,
        candidate.platform_stop_tuple,
        candidate.policy_key.encode(),
    )


def _schedule_only(
    candidate: CandidateItinerary,
    *,
    ready_at: datetime,
    reference_planned: int,
) -> SelectedItinerary:
    planned = _planned(candidate, ready_at)
    return SelectedItinerary(
        candidate.policy_key,
        planned,
        planned - reference_planned,
        None,
        None,
        (),
        "NOT_SELECTED_OUTPUT_UNVALIDATED",
    )


def select_recovery_decision(
    candidates: tuple[CandidateItinerary, ...],
    *,
    continuation_policy_key: str,
    context: DecisionContext,
    trigger: RecoveryTriggerInput,
    maximum_extra_time_seconds: int = 1_200,
) -> RecoveryDecision | None:
    """Return a schedule-only recovery action after a supported V1 trigger."""

    reasons = recovery_reasons(trigger)
    if not reasons:
        return None
    if not 0 <= maximum_extra_time_seconds <= 1_200:
        raise ValueError("recovery cap must be from zero through 20 minutes")
    by_key = {candidate.policy_key: candidate for candidate in candidates}
    if len(by_key) != len(candidates):
        raise ValueError("recovery candidates must have unique policy keys")
    continuation = by_key.get(continuation_policy_key)
    if continuation is None:
        raise ValueError("continuation must belong to the frozen recovery universe")
    mask = context.mask
    if any(key not in mask for key in by_key):
        raise ValueError("recovery context must classify every candidate")
    eligible = tuple(candidate for candidate in candidates if mask[candidate.policy_key])
    winning = reasons[0]
    if winning is RecoveryReason.LOW_TRANSFER_PROBABILITY and mask[continuation_policy_key]:
        cap_reference = continuation
    else:
        if not eligible:
            cap_reference = continuation
        else:
            cap_reference = min(eligible, key=lambda item: _rank(item, context.decision_cutoff_utc))
    reference_planned = _planned(cap_reference, context.decision_cutoff_utc)
    continuation_output = _schedule_only(
        continuation,
        ready_at=context.decision_cutoff_utc,
        reference_planned=reference_planned,
    )
    reference_output = _schedule_only(
        cap_reference,
        ready_at=context.decision_cutoff_utc,
        reference_planned=reference_planned,
    )
    selectable = tuple(
        candidate
        for candidate in eligible
        if candidate.policy_key != continuation_policy_key
        and _planned(candidate, context.decision_cutoff_utc) - reference_planned
        <= maximum_extra_time_seconds
    )
    ordered = tuple(sorted(selectable, key=lambda item: _rank(item, context.decision_cutoff_utc)))
    if not ordered:
        return RecoveryDecision(
            reasons,
            winning,
            RecoveryStatus.NO_DISTINCT_RECOVERY_ACTION,
            continuation_output,
            reference_output,
            (),
            None,
            None,
        )
    recommendation = _schedule_only(
        ordered[0],
        ready_at=context.decision_cutoff_utc,
        reference_planned=reference_planned,
    )
    backup = (
        _schedule_only(
            ordered[1],
            ready_at=context.decision_cutoff_utc,
            reference_planned=reference_planned,
        )
        if len(ordered) > 1
        else None
    )
    return RecoveryDecision(
        reasons,
        winning,
        RecoveryStatus.RECOVERY_ACTION_AVAILABLE,
        continuation_output,
        reference_output,
        tuple(candidate.policy_key for candidate in ordered),
        recommendation,
        backup,
    )


def next_trip_state(
    current: TripState,
    requested: TripState,
    *,
    active_transfer_count: int,
    activating_recovery_transfer_count: int | None = None,
    stop_requested: bool = False,
) -> TripState:
    """Validate one edge without inferring rider location or allowing rollback."""

    if current is TripState.ENDED or requested is current:
        raise ValueError("trip state transition is not allowed")
    if stop_requested:
        if requested is not TripState.ENDED:
            raise ValueError("stop requests must transition to ENDED")
        return requested
    if requested is TripState.ENDED:
        raise ValueError("ENDED is reachable only through the stop operation")
    if current is TripState.NOT_STARTED:
        expected = TripState.ON_FIRST_LEG if active_transfer_count == 1 else TripState.ON_FINAL_LEG
    elif current is TripState.ON_FIRST_LEG:
        expected = TripState.AT_TRANSFER
    elif current is TripState.AT_TRANSFER:
        transfer_count = (
            activating_recovery_transfer_count
            if activating_recovery_transfer_count is not None
            else active_transfer_count
        )
        expected = TripState.ON_FIRST_LEG if transfer_count == 1 else TripState.ON_FINAL_LEG
    else:
        raise ValueError("trip state transition is not allowed")
    if requested is not expected:
        raise ValueError("trip state transition is not allowed")
    return requested
