from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from arrive90_data_contracts.candidates import CandidateItinerary, TransitLeg
from arrive90_decision.contracts import (
    DecisionContext,
    RecoveryReason,
    RecoveryStatus,
    RecoveryTriggerInput,
    TripState,
)
from arrive90_decision.recovery import (
    next_trip_state,
    recovery_reasons,
    select_recovery_decision,
)

NOW = datetime(2025, 1, 1, 12, tzinfo=UTC)


def _candidate(index: int, arrival_minutes: int) -> CandidateItinerary:
    leg = TransitLeg(
        f"pattern-{index}",
        "Red",
        0,
        f"trip-{index}",
        "a",
        "station-a",
        "b",
        "station-b",
        NOW + timedelta(minutes=index),
        NOW + timedelta(minutes=arrival_minutes),
        ("a", "b"),
    )
    return CandidateItinerary((leg,), ())


def _trigger(**changes: object) -> RecoveryTriggerInput:
    values: dict[str, object] = {
        "state": TripState.AT_TRANSFER,
        "closure_applies": False,
        "rounded_transfer_probability": Decimal("0.490000"),
        "candidate_decile_supported": True,
        "station_supported": True,
        "selected_policy_decile_supported": True,
        "original_confirmatory_policy": True,
        "recovery_ever_activated": False,
    }
    values.update(changes)
    return RecoveryTriggerInput(**values)  # type: ignore[arg-type]


def _context(
    candidates: tuple[CandidateItinerary, ...], masked: frozenset[str] = frozenset()
) -> DecisionContext:
    return DecisionContext(
        NOW,
        "recovery-context",
        "ALERT_MASK_V1",
        "manifest",
        tuple(
            (candidate.policy_key, candidate.policy_key not in masked) for candidate in candidates
        ),
    )


def test_recovery_reason_precedence_and_support_conjunction() -> None:
    both = recovery_reasons(_trigger(closure_applies=True))
    assert both == (
        RecoveryReason.CAUSAL_CLOSURE,
        RecoveryReason.LOW_TRANSFER_PROBABILITY,
    )
    assert recovery_reasons(_trigger(state=TripState.ON_FIRST_LEG)) == ()
    assert recovery_reasons(_trigger(station_supported=False)) == ()
    assert recovery_reasons(_trigger(recovery_ever_activated=True)) == ()


def test_low_probability_recovery_uses_continuation_as_cap_reference() -> None:
    continuation = _candidate(0, 20)
    fastest = _candidate(1, 15)
    backup = _candidate(2, 18)
    candidates = (continuation, backup, fastest)
    result = select_recovery_decision(
        candidates,
        continuation_policy_key=continuation.policy_key,
        context=_context(candidates),
        trigger=_trigger(),
    )
    assert result is not None
    assert result.status is RecoveryStatus.RECOVERY_ACTION_AVAILABLE
    assert result.cap_reference.policy_key == continuation.policy_key
    assert result.recommendation is not None
    assert result.recommendation.policy_key == fastest.policy_key
    assert result.recommendation.extra_planned_time_seconds == -300
    assert result.recommendation.deadline_probability is None
    assert result.backup_itinerary is not None
    assert result.backup_itinerary.policy_key == backup.policy_key
    assert result.canonical_payload() == result.canonical_payload()


def test_closure_uses_static_fastest_reference_and_never_selects_continuation() -> None:
    continuation = _candidate(0, 12)
    next_best = _candidate(1, 15)
    later = _candidate(2, 40)
    candidates = (continuation, next_best, later)
    result = select_recovery_decision(
        candidates,
        continuation_policy_key=continuation.policy_key,
        context=_context(candidates, frozenset({continuation.policy_key})),
        trigger=_trigger(closure_applies=True, rounded_transfer_probability=None),
        maximum_extra_time_seconds=1_200,
    )
    assert result is not None
    assert result.winning_reason is RecoveryReason.CAUSAL_CLOSURE
    assert result.cap_reference.policy_key == next_best.policy_key
    assert result.recommendation is not None
    assert result.recommendation.policy_key == next_best.policy_key
    assert later.policy_key not in result.selectable_policy_keys
    assert result.continuation_comparator.policy_key == continuation.policy_key


def test_no_distinct_action_and_invalid_inputs() -> None:
    continuation = _candidate(0, 20)
    result = select_recovery_decision(
        (continuation,),
        continuation_policy_key=continuation.policy_key,
        context=_context((continuation,)),
        trigger=_trigger(),
    )
    assert result is not None
    assert result.status is RecoveryStatus.NO_DISTINCT_RECOVERY_ACTION
    assert result.recommendation is None
    assert (
        select_recovery_decision(
            (continuation,),
            continuation_policy_key=continuation.policy_key,
            context=_context((continuation,)),
            trigger=_trigger(state=TripState.NOT_STARTED),
        )
        is None
    )
    with pytest.raises(ValueError, match="continuation"):
        select_recovery_decision(
            (continuation,),
            continuation_policy_key="unknown",
            context=_context((continuation,)),
            trigger=_trigger(),
        )


def test_trip_state_graph_for_direct_transfer_recovery_and_stop() -> None:
    assert (
        next_trip_state(
            TripState.NOT_STARTED,
            TripState.ON_FINAL_LEG,
            active_transfer_count=0,
        )
        is TripState.ON_FINAL_LEG
    )
    assert (
        next_trip_state(
            TripState.NOT_STARTED,
            TripState.ON_FIRST_LEG,
            active_transfer_count=1,
        )
        is TripState.ON_FIRST_LEG
    )
    assert (
        next_trip_state(
            TripState.ON_FIRST_LEG,
            TripState.AT_TRANSFER,
            active_transfer_count=1,
        )
        is TripState.AT_TRANSFER
    )
    assert (
        next_trip_state(
            TripState.AT_TRANSFER,
            TripState.ON_FIRST_LEG,
            active_transfer_count=1,
            activating_recovery_transfer_count=1,
        )
        is TripState.ON_FIRST_LEG
    )
    assert (
        next_trip_state(
            TripState.AT_TRANSFER,
            TripState.ON_FINAL_LEG,
            active_transfer_count=1,
            activating_recovery_transfer_count=0,
        )
        is TripState.ON_FINAL_LEG
    )
    assert (
        next_trip_state(
            TripState.ON_FINAL_LEG,
            TripState.ENDED,
            active_transfer_count=0,
            stop_requested=True,
        )
        is TripState.ENDED
    )
    with pytest.raises(ValueError, match="only through"):
        next_trip_state(TripState.ON_FINAL_LEG, TripState.ENDED, active_transfer_count=0)
    with pytest.raises(ValueError, match="not allowed"):
        next_trip_state(TripState.ENDED, TripState.ON_FINAL_LEG, active_transfer_count=0)
