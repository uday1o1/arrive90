"""Deterministic virtual-rider oracle over primary source observations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from arrive90_data_contracts.candidates import CandidateItinerary, HistoricalBaseQuery
from arrive90_data_contracts.realtime import CompletenessStatus, require_utc
from arrive90_data_contracts.schedule import NormalizedStopEvidence
from arrive90_routing.exceptional import (
    EligibilityDecision,
    ExceptionalTripState,
    trip_eligibility,
)

from arrive90_outcomes.contracts import (
    DeadlineLabelStatus,
    JourneyStatus,
    OutcomeRow,
    OutcomeTimeSemantics,
    TransferLabelStatus,
)


@dataclass(frozen=True)
class RealizedTrainPath:
    observed_trip_id: str
    route_pattern_id: str
    boarding_stop_id: str
    destination_stop_id: str
    boarding_evidence: NormalizedStopEvidence | None
    destination_lower_bound_utc: datetime | None
    destination_upper_bound_utc: datetime | None
    destination_evidence_class: str
    exceptional_state: ExceptionalTripState = ExceptionalTripState.SCHEDULED
    serves_complete_policy_path: bool = True

    def __post_init__(self) -> None:
        for field, value in (
            ("destination_lower_bound_utc", self.destination_lower_bound_utc),
            ("destination_upper_bound_utc", self.destination_upper_bound_utc),
        ):
            if value is not None:
                require_utc(value, field)
        if (
            self.destination_lower_bound_utc is not None
            and self.destination_upper_bound_utc is not None
            and self.destination_upper_bound_utc < self.destination_lower_bound_utc
        ):
            raise ValueError("realized destination interval is inverted")


class OutcomeResolver:
    def __init__(
        self,
        *,
        oracle_policy_version: str = "virtual-rider-v1",
        outcome_version: str = "outcome-v1",
    ) -> None:
        self.oracle_policy_version = oracle_policy_version
        self.outcome_version = outcome_version

    @staticmethod
    def _eligible_for_leg(
        train: RealizedTrainPath,
        pattern_id: str,
        boarding_stop_id: str,
        destination_stop_id: str,
        ready_at_utc: datetime,
    ) -> bool:
        evidence = train.boarding_evidence
        return (
            train.route_pattern_id == pattern_id
            and train.boarding_stop_id == boarding_stop_id
            and train.destination_stop_id == destination_stop_id
            and trip_eligibility(
                train.exceptional_state,
                serves_complete_policy_path=train.serves_complete_policy_path,
            )
            is EligibilityDecision.ELIGIBLE
            and evidence is not None
            and evidence.usable_for_primary_boarding
            and evidence.stop_id == boarding_stop_id
            and evidence.arrival_upper_bound_utc is not None
            and evidence.arrival_upper_bound_utc >= ready_at_utc
        )

    def resolve(
        self,
        *,
        query: HistoricalBaseQuery,
        candidate: CandidateItinerary,
        deadline_utc: datetime,
        trains: tuple[RealizedTrainPath, ...],
        completeness: CompletenessStatus,
        observation_complete_through_utc: datetime | None,
        semantics: OutcomeTimeSemantics,
        outcome_resolved_at_utc: datetime,
    ) -> OutcomeRow:
        require_utc(deadline_utc, "deadline_utc")
        require_utc(outcome_resolved_at_utc, "outcome_resolved_at_utc")
        if observation_complete_through_utc is not None:
            require_utc(observation_complete_through_utc, "observation_complete_through_utc")
        ready = query.ready_at_utc
        boarding_ids: list[str] = []
        transfer_status = TransferLabelStatus.NOT_APPLICABLE
        transfer_success: bool | None = None
        destination_lower: datetime | None = None
        destination_upper: datetime | None = None
        destination_evidence = "UNKNOWN"
        censoring_reason: str | None = None
        reached_transfer = False
        for leg_index, leg in enumerate(candidate.legs):
            if leg_index == 1:
                reached_transfer = True
            ambiguous = [
                train
                for train in trains
                if train.route_pattern_id == leg.route_pattern_id
                and train.boarding_stop_id == leg.boarding_stop_id
                and train.destination_stop_id == leg.alighting_stop_id
                and trip_eligibility(train.exceptional_state) is EligibilityDecision.CENSORED
            ]
            eligible = [
                train
                for train in trains
                if self._eligible_for_leg(
                    train,
                    leg.route_pattern_id,
                    leg.boarding_stop_id,
                    leg.alighting_stop_id,
                    ready,
                )
            ]
            if ambiguous:
                censoring_reason = "AMBIGUOUS_ELIGIBLE_TRAIN"
                break
            if not eligible:
                censoring_reason = "NO_ELIGIBLE_TRAIN_OBSERVED"
                break

            def boarding_order(train: RealizedTrainPath) -> tuple[datetime, bytes]:
                evidence = train.boarding_evidence
                if evidence is None or evidence.arrival_upper_bound_utc is None:
                    raise AssertionError("eligible train lost its boarding evidence")
                return evidence.arrival_upper_bound_utc, train.observed_trip_id.encode()

            selected = min(eligible, key=boarding_order)
            evidence = selected.boarding_evidence
            if evidence is None or evidence.arrival_upper_bound_utc is None:
                raise AssertionError("eligible train lost its boarding evidence")
            boarding_ids.append(evidence.source_row_key)
            if leg_index == 1:
                transfer_success = evidence.arrival_upper_bound_utc <= ready + timedelta(minutes=15)
                transfer_status = (
                    TransferLabelStatus.SUCCESS_IDENTIFIED
                    if transfer_success
                    else TransferLabelStatus.FAILURE_IDENTIFIED
                )
            destination_lower = selected.destination_lower_bound_utc
            destination_upper = selected.destination_upper_bound_utc
            destination_evidence = selected.destination_evidence_class
            if destination_lower is None or destination_upper is None:
                censoring_reason = "MISSING_DESTINATION_INTERVAL"
                break
            if leg_index < len(candidate.transfer_walk_seconds):
                ready = destination_upper + timedelta(
                    seconds=candidate.transfer_walk_seconds[leg_index]
                )
        if censoring_reason is not None:
            if (
                completeness is CompletenessStatus.COMPLETE
                and censoring_reason == "NO_ELIGIBLE_TRAIN_OBSERVED"
            ):
                journey_status = JourneyStatus.PROVEN_NO_ARRIVAL_WITHIN_HORIZON
                deadline_status = DeadlineLabelStatus.FAILURE_IDENTIFIED
                deadline_success: bool | None = False
                censoring_reason = None
                if reached_transfer:
                    transfer_status = TransferLabelStatus.FAILURE_IDENTIFIED
                    transfer_success = False
            else:
                journey_status = JourneyStatus.CENSORED
                deadline_status = DeadlineLabelStatus.JOURNEY_CENSORED
                deadline_success = None
                if reached_transfer and transfer_status is TransferLabelStatus.NOT_APPLICABLE:
                    transfer_status = TransferLabelStatus.WINDOW_CENSORED
            destination_lower = None
            destination_upper = None
            lateness_lower = None
            lateness_upper = None
        else:
            if destination_lower is None or destination_upper is None:
                raise AssertionError("resolved journey lost its destination interval")
            journey_status = JourneyStatus.ARRIVED
            if destination_upper <= deadline_utc:
                deadline_status = DeadlineLabelStatus.SUCCESS_IDENTIFIED
                deadline_success = True
            elif destination_lower > deadline_utc:
                deadline_status = DeadlineLabelStatus.FAILURE_IDENTIFIED
                deadline_success = False
            else:
                deadline_status = DeadlineLabelStatus.INTERVAL_UNRESOLVED
                deadline_success = None
            lateness_lower = max(0, int((destination_lower - deadline_utc).total_seconds()))
            lateness_upper = max(0, int((destination_upper - deadline_utc).total_seconds()))
        return OutcomeRow(
            query_id=query.query_id,
            itinerary_id=candidate.policy_key,
            first_boarding_observation_evidence_id=boarding_ids[0] if boarding_ids else None,
            transfer_boarding_observation_evidence_id=(
                boarding_ids[1] if len(boarding_ids) > 1 else None
            ),
            destination_arrival_lower_bound_utc=destination_lower,
            destination_arrival_upper_bound_utc=destination_upper,
            deadline_label_status=deadline_status,
            transfer_label_status=transfer_status,
            transfer_success=transfer_success,
            deadline_success=deadline_success,
            lateness_lower_bound_seconds=lateness_lower,
            lateness_upper_bound_seconds=lateness_upper,
            backup_used=False,
            journey_status=journey_status,
            observation_complete_through_utc=observation_complete_through_utc,
            censoring_reason=censoring_reason,
            label_evidence_class=destination_evidence,
            outcome_time_semantics=semantics,
            oracle_policy_version=self.oracle_policy_version,
            outcome_resolved_at_utc=outcome_resolved_at_utc,
            outcome_version=self.outcome_version,
        )
