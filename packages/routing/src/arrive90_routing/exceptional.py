"""Frozen exceptional-trip eligibility decision table."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum


class ExceptionalTripState(StrEnum):
    SCHEDULED = "SCHEDULED"
    ADDED = "ADDED"
    REPLACEMENT = "REPLACEMENT"
    CANCELED = "CANCELED"
    SKIPPED = "SKIPPED"
    SHORT_TURNED = "SHORT_TURNED"
    NON_REVENUE = "NON_REVENUE"
    UNMATCHED = "UNMATCHED"


class EligibilityDecision(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    EXCLUDED = "EXCLUDED"
    REQUIRE_FULL_PATH_PROOF = "REQUIRE_FULL_PATH_PROOF"
    CENSORED = "CENSORED"


EXCEPTIONAL_TRIP_TABLE = {
    ExceptionalTripState.SCHEDULED: EligibilityDecision.ELIGIBLE,
    ExceptionalTripState.ADDED: EligibilityDecision.EXCLUDED,
    ExceptionalTripState.REPLACEMENT: EligibilityDecision.EXCLUDED,
    ExceptionalTripState.CANCELED: EligibilityDecision.EXCLUDED,
    ExceptionalTripState.SKIPPED: EligibilityDecision.EXCLUDED,
    ExceptionalTripState.SHORT_TURNED: EligibilityDecision.REQUIRE_FULL_PATH_PROOF,
    ExceptionalTripState.NON_REVENUE: EligibilityDecision.EXCLUDED,
    ExceptionalTripState.UNMATCHED: EligibilityDecision.CENSORED,
}


def exceptional_trip_table_hash() -> str:
    payload = json.dumps(
        {state.value: decision.value for state, decision in EXCEPTIONAL_TRIP_TABLE.items()},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def trip_eligibility(
    state: ExceptionalTripState, *, serves_complete_policy_path: bool = False
) -> EligibilityDecision:
    decision = EXCEPTIONAL_TRIP_TABLE[state]
    if decision is EligibilityDecision.REQUIRE_FULL_PATH_PROOF:
        return (
            EligibilityDecision.ELIGIBLE
            if serves_complete_policy_path
            else EligibilityDecision.EXCLUDED
        )
    return decision
