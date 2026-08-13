from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest
from arrive90_data_contracts.candidates import CandidateItinerary, HistoricalBaseQuery, TransitLeg
from arrive90_data_contracts.realtime import CompletenessStatus
from arrive90_data_contracts.schedule import (
    ArrivalEvidence,
    DepartureEvidence,
    IntervalClosure,
    NormalizedStopEvidence,
)
from arrive90_outcomes.contracts import (
    DeadlineLabelStatus,
    JourneyStatus,
    OutcomeRow,
    OutcomeTimeSemantics,
    TransferLabelStatus,
)
from arrive90_outcomes.oracle import OutcomeResolver, RealizedTrainPath
from arrive90_routing.exceptional import ExceptionalTripState

NOW = datetime(2025, 1, 1, 12, tzinfo=UTC)


def _query() -> HistoricalBaseQuery:
    return HistoricalBaseQuery(
        "query",
        NOW,
        date(2025, 1, 1),
        "A",
        "B",
        NOW,
        NOW + timedelta(minutes=210),
        "schedule",
        "v1",
        "Red",
        1.0,
        "test",
    )


def _leg(
    pattern: str = "red",
    route: str = "Red",
    board: str = "a",
    board_station: str = "A",
    destination: str = "b",
    destination_station: str = "B",
    offset: int = 1,
) -> TransitLeg:
    return TransitLeg(
        pattern,
        route,
        0,
        f"scheduled-{pattern}",
        board,
        board_station,
        destination,
        destination_station,
        NOW + timedelta(minutes=offset),
        NOW + timedelta(minutes=offset + 10),
        (board, destination),
    )


def _direct_candidate() -> CandidateItinerary:
    return CandidateItinerary((_leg(),), ())


def _evidence(
    key: str,
    stop: str,
    observed_at: datetime,
    *,
    direct: bool = True,
) -> NormalizedStopEvidence:
    return NormalizedStopEvidence(
        key,
        "observed-trip",
        stop,
        1,
        observed_at if direct else None,
        observed_at,
        IntervalClosure.EXACT if direct else IntervalClosure.LEFT_OPEN_RIGHT_CLOSED,
        (
            ArrivalEvidence.VP_STOPPED_AT
            if direct
            else ArrivalEvidence.VP_DEPARTED_STATION_UPPER_BOUND
        ),
        None if direct else observed_at,
        DepartureEvidence.UNKNOWN if direct else DepartureEvidence.DOWNSTREAM_MOVE_UPPER_BOUND,
        observed_at,
        direct,
    )


def _train(
    *,
    pattern: str = "red",
    board: str = "a",
    destination: str = "b",
    board_at: datetime = NOW,
    arrival_lower: datetime | None = None,
    arrival_upper: datetime | None = None,
    direct: bool = True,
    state: ExceptionalTripState = ExceptionalTripState.SCHEDULED,
) -> RealizedTrainPath:
    return RealizedTrainPath(
        f"train-{pattern}-{board_at.isoformat()}",
        pattern,
        board,
        destination,
        _evidence(f"evidence-{pattern}", board, board_at, direct=direct),
        arrival_lower or NOW + timedelta(minutes=9),
        arrival_upper or NOW + timedelta(minutes=10),
        "VP_STOPPED_AT",
        state,
    )


def _resolve(
    trains: tuple[RealizedTrainPath, ...],
    *,
    deadline: datetime = NOW + timedelta(minutes=12),
    completeness: CompletenessStatus = CompletenessStatus.COMPLETE,
    candidate: CandidateItinerary | None = None,
) -> OutcomeRow:
    return OutcomeResolver().resolve(
        query=_query(),
        candidate=candidate or _direct_candidate(),
        deadline_utc=deadline,
        trains=trains,
        completeness=completeness,
        observation_complete_through_utc=NOW + timedelta(minutes=210),
        semantics=OutcomeTimeSemantics.VP_STOP_OBSERVATION_INTERVAL,
        outcome_resolved_at_utc=NOW + timedelta(days=1),
    )


def test_direct_stop_evidence_resolves_success_and_lateness_interval() -> None:
    result = _resolve((_train(board_at=NOW),))
    assert result.journey_status is JourneyStatus.ARRIVED
    assert result.deadline_label_status is DeadlineLabelStatus.SUCCESS_IDENTIFIED
    assert result.deadline_success is True
    assert result.lateness_lower_bound_seconds == 0
    assert result.first_boarding_observation_evidence_id == "evidence-red"


def test_deadline_failure_and_interval_unresolved_are_not_conflated() -> None:
    train = _train(
        arrival_lower=NOW + timedelta(minutes=14),
        arrival_upper=NOW + timedelta(minutes=16),
    )
    failure = _resolve((train,), deadline=NOW + timedelta(minutes=13))
    assert failure.deadline_label_status is DeadlineLabelStatus.FAILURE_IDENTIFIED
    assert failure.deadline_success is False
    unresolved = _resolve((train,), deadline=NOW + timedelta(minutes=15))
    assert unresolved.deadline_label_status is DeadlineLabelStatus.INTERVAL_UNRESOLVED
    assert unresolved.deadline_success is None


def test_downstream_move_never_becomes_boarding_evidence() -> None:
    move_only = _train(board_at=NOW + timedelta(minutes=1), direct=False)
    result = _resolve((move_only,))
    assert result.journey_status is JourneyStatus.PROVEN_NO_ARRIVAL_WITHIN_HORIZON
    assert result.deadline_success is False
    assert result.first_boarding_observation_evidence_id is None


def test_incomplete_or_ambiguous_windows_censor_instead_of_fabricating_failure() -> None:
    incomplete = _resolve((), completeness=CompletenessStatus.INCOMPLETE)
    assert incomplete.journey_status is JourneyStatus.CENSORED
    assert incomplete.deadline_label_status is DeadlineLabelStatus.JOURNEY_CENSORED
    assert incomplete.censoring_reason == "NO_ELIGIBLE_TRAIN_OBSERVED"
    ambiguous = _resolve((_train(state=ExceptionalTripState.UNMATCHED),))
    assert ambiguous.journey_status is JourneyStatus.CENSORED
    assert ambiguous.censoring_reason == "AMBIGUOUS_ELIGIBLE_TRAIN"


def test_transfer_outcome_is_conditional_on_reaching_transfer() -> None:
    first = _leg(destination="x-red", destination_station="X")
    second = _leg(
        pattern="orange",
        route="Orange",
        board="x-orange",
        board_station="X",
        destination="c",
        destination_station="C",
        offset=20,
    )
    candidate = CandidateItinerary((first, second), (120,))
    first_train = _train(
        destination="x-red",
        arrival_lower=NOW + timedelta(minutes=8),
        arrival_upper=NOW + timedelta(minutes=9),
    )
    second_train = _train(
        pattern="orange",
        board="x-orange",
        destination="c",
        board_at=NOW + timedelta(minutes=12),
        arrival_lower=NOW + timedelta(minutes=20),
        arrival_upper=NOW + timedelta(minutes=21),
    )
    result = _resolve(
        (second_train, first_train), candidate=candidate, deadline=NOW + timedelta(minutes=25)
    )
    assert result.transfer_label_status is TransferLabelStatus.SUCCESS_IDENTIFIED
    assert result.transfer_success is True
    assert result.transfer_boarding_observation_evidence_id == "evidence-orange"

    never_reached = _resolve(
        (second_train,), candidate=candidate, completeness=CompletenessStatus.INCOMPLETE
    )
    assert never_reached.transfer_label_status is TransferLabelStatus.NOT_APPLICABLE
    assert never_reached.transfer_success is None


def test_realized_path_rejects_inverted_destination_interval() -> None:
    with pytest.raises(ValueError, match="inverted"):
        replace(
            _train(),
            destination_lower_bound_utc=NOW + timedelta(minutes=2),
            destination_upper_bound_utc=NOW + timedelta(minutes=1),
        )
