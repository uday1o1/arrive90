from __future__ import annotations

from arrive90_routing.exceptional import (
    EligibilityDecision,
    ExceptionalTripState,
    exceptional_trip_table_hash,
    trip_eligibility,
)


def test_exceptional_trip_table_is_frozen_and_short_turn_requires_path_proof() -> None:
    assert trip_eligibility(ExceptionalTripState.SCHEDULED) is EligibilityDecision.ELIGIBLE
    assert trip_eligibility(ExceptionalTripState.CANCELED) is EligibilityDecision.EXCLUDED
    assert trip_eligibility(ExceptionalTripState.UNMATCHED) is EligibilityDecision.CENSORED
    assert trip_eligibility(ExceptionalTripState.SHORT_TURNED) is EligibilityDecision.EXCLUDED
    assert (
        trip_eligibility(ExceptionalTripState.SHORT_TURNED, serves_complete_policy_path=True)
        is EligibilityDecision.ELIGIBLE
    )
    assert len(exceptional_trip_table_hash()) == 64
