"""Versioned interval and censoring contracts for virtual-rider outcomes."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from arrive90_data_contracts.realtime import require_utc


class DeadlineLabelStatus(StrEnum):
    SUCCESS_IDENTIFIED = "SUCCESS_IDENTIFIED"
    FAILURE_IDENTIFIED = "FAILURE_IDENTIFIED"
    INTERVAL_UNRESOLVED = "INTERVAL_UNRESOLVED"
    JOURNEY_CENSORED = "JOURNEY_CENSORED"


class TransferLabelStatus(StrEnum):
    SUCCESS_IDENTIFIED = "SUCCESS_IDENTIFIED"
    FAILURE_IDENTIFIED = "FAILURE_IDENTIFIED"
    WINDOW_CENSORED = "WINDOW_CENSORED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class JourneyStatus(StrEnum):
    ARRIVED = "ARRIVED"
    PROVEN_NO_ARRIVAL_WITHIN_HORIZON = "PROVEN_NO_ARRIVAL_WITHIN_HORIZON"
    CENSORED = "CENSORED"


class OutcomeTimeSemantics(StrEnum):
    VP_STOP_OBSERVATION_INTERVAL = "VP_STOP_OBSERVATION_INTERVAL"
    CONSERVATIVE_STATION_DEPARTURE_INTERVAL = "CONSERVATIVE_STATION_DEPARTURE_INTERVAL"


@dataclass(frozen=True)
class OutcomeRow:
    query_id: str
    itinerary_id: str
    first_boarding_observation_evidence_id: str | None
    transfer_boarding_observation_evidence_id: str | None
    destination_arrival_lower_bound_utc: datetime | None
    destination_arrival_upper_bound_utc: datetime | None
    deadline_label_status: DeadlineLabelStatus
    transfer_label_status: TransferLabelStatus
    transfer_success: bool | None
    deadline_success: bool | None
    lateness_lower_bound_seconds: int | None
    lateness_upper_bound_seconds: int | None
    backup_used: bool
    journey_status: JourneyStatus
    observation_complete_through_utc: datetime | None
    censoring_reason: str | None
    label_evidence_class: str
    outcome_time_semantics: OutcomeTimeSemantics
    oracle_policy_version: str
    outcome_resolved_at_utc: datetime
    outcome_version: str

    def __post_init__(self) -> None:
        for field, value in (
            ("destination_arrival_lower_bound_utc", self.destination_arrival_lower_bound_utc),
            ("destination_arrival_upper_bound_utc", self.destination_arrival_upper_bound_utc),
            ("observation_complete_through_utc", self.observation_complete_through_utc),
            ("outcome_resolved_at_utc", self.outcome_resolved_at_utc),
        ):
            if value is not None:
                require_utc(value, field)
        if self.journey_status is JourneyStatus.ARRIVED:
            if (
                self.destination_arrival_lower_bound_utc is None
                or self.destination_arrival_upper_bound_utc is None
            ):
                raise ValueError("arrived outcome requires a finite destination interval")
            if self.destination_arrival_upper_bound_utc < self.destination_arrival_lower_bound_utc:
                raise ValueError("destination arrival interval is inverted")
        if self.journey_status is JourneyStatus.CENSORED and self.censoring_reason is None:
            raise ValueError("censored journey requires an explicit reason")
        identified = self.deadline_label_status in {
            DeadlineLabelStatus.SUCCESS_IDENTIFIED,
            DeadlineLabelStatus.FAILURE_IDENTIFIED,
        }
        if identified != (self.deadline_success is not None):
            raise ValueError("deadline success must exist exactly for identified labels")
        transfer_identified = self.transfer_label_status in {
            TransferLabelStatus.SUCCESS_IDENTIFIED,
            TransferLabelStatus.FAILURE_IDENTIFIED,
        }
        if transfer_identified != (self.transfer_success is not None):
            raise ValueError("transfer success must exist exactly for identified labels")


@dataclass(frozen=True)
class AftRow:
    training_key: str
    lower_bound_seconds: float | None
    upper_bound_seconds: float | None
    assigned_weight: float
    included_in_likelihood: bool
    exclusion_reason: str | None

    def __post_init__(self) -> None:
        if self.assigned_weight <= 0:
            raise ValueError("AFT row weight must be positive")
        if self.included_in_likelihood:
            if self.lower_bound_seconds is None or self.upper_bound_seconds is None:
                raise ValueError("included AFT row requires both bounds")
            if self.lower_bound_seconds <= 0 or self.upper_bound_seconds < self.lower_bound_seconds:
                raise ValueError("included AFT bounds must satisfy 0 < lower <= upper")
            if math.isinf(self.lower_bound_seconds):
                raise ValueError("AFT lower bound must be finite")
        elif self.exclusion_reason is None:
            raise ValueError("excluded AFT row requires a reason")
