"""Convert interval outcomes to valid AFT likelihood rows without fabrication."""

from __future__ import annotations

import math
from datetime import datetime

from arrive90_data_contracts.realtime import require_utc

from arrive90_outcomes.contracts import AftRow, JourneyStatus, OutcomeRow


def build_aft_row(
    outcome: OutcomeRow,
    *,
    ready_at_utc: datetime,
    observation_horizon_utc: datetime,
    base_query_weight: float,
    candidate_count: int,
) -> AftRow:
    require_utc(ready_at_utc, "ready_at_utc")
    require_utc(observation_horizon_utc, "observation_horizon_utc")
    if candidate_count <= 0:
        raise ValueError("candidate count must be positive")
    weight = base_query_weight / candidate_count
    training_key = f"{outcome.query_id}:{outcome.itinerary_id}"
    if outcome.journey_status is JourneyStatus.ARRIVED:
        if (
            outcome.destination_arrival_lower_bound_utc is None
            or outcome.destination_arrival_upper_bound_utc is None
        ):
            raise AssertionError("arrived outcome has no interval")
        lower = (outcome.destination_arrival_lower_bound_utc - ready_at_utc).total_seconds()
        upper = (outcome.destination_arrival_upper_bound_utc - ready_at_utc).total_seconds()
        return AftRow(training_key, lower, upper, weight, True, None)
    if outcome.journey_status is JourneyStatus.PROVEN_NO_ARRIVAL_WITHIN_HORIZON:
        lower = (observation_horizon_utc - ready_at_utc).total_seconds()
        return AftRow(training_key, lower, math.inf, weight, True, None)
    if (
        outcome.observation_complete_through_utc is not None
        and outcome.observation_complete_through_utc > ready_at_utc
    ):
        lower = (outcome.observation_complete_through_utc - ready_at_utc).total_seconds()
        return AftRow(training_key, lower, math.inf, weight, True, None)
    return AftRow(training_key, None, None, weight, False, outcome.censoring_reason)
