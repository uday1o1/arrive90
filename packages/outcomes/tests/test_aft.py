from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from arrive90_outcomes.aft import build_aft_row
from arrive90_outcomes.contracts import (
    AftRow,
    DeadlineLabelStatus,
    JourneyStatus,
    OutcomeRow,
    OutcomeTimeSemantics,
    TransferLabelStatus,
)

NOW = datetime(2025, 1, 1, tzinfo=UTC)


def _outcome(status: JourneyStatus) -> OutcomeRow:
    arrived = status is JourneyStatus.ARRIVED
    censored = status is JourneyStatus.CENSORED
    return OutcomeRow(
        "query",
        "policy",
        "boarding" if arrived else None,
        None,
        NOW + timedelta(seconds=60) if arrived else None,
        NOW + timedelta(seconds=120) if arrived else None,
        (
            DeadlineLabelStatus.SUCCESS_IDENTIFIED
            if arrived
            else (
                DeadlineLabelStatus.JOURNEY_CENSORED
                if censored
                else DeadlineLabelStatus.FAILURE_IDENTIFIED
            )
        ),
        TransferLabelStatus.NOT_APPLICABLE,
        None,
        True if arrived else (None if censored else False),
        0 if arrived else None,
        0 if arrived else None,
        False,
        status,
        NOW + timedelta(seconds=300) if censored else NOW + timedelta(seconds=600),
        "SOURCE_GAP" if censored else None,
        "VP_STOPPED_AT",
        OutcomeTimeSemantics.VP_STOP_OBSERVATION_INTERVAL,
        "oracle-v1",
        NOW + timedelta(days=1),
        "outcome-v1",
    )


def test_arrived_and_proven_nonarrival_use_interval_and_right_censored_rows() -> None:
    arrived = build_aft_row(
        _outcome(JourneyStatus.ARRIVED),
        ready_at_utc=NOW,
        observation_horizon_utc=NOW + timedelta(seconds=600),
        base_query_weight=1,
        candidate_count=2,
    )
    assert (arrived.lower_bound_seconds, arrived.upper_bound_seconds) == (60, 120)
    assert arrived.assigned_weight == 0.5
    no_arrival = build_aft_row(
        _outcome(JourneyStatus.PROVEN_NO_ARRIVAL_WITHIN_HORIZON),
        ready_at_utc=NOW,
        observation_horizon_utc=NOW + timedelta(seconds=600),
        base_query_weight=1,
        candidate_count=1,
    )
    assert no_arrival.lower_bound_seconds == 600
    assert math.isinf(no_arrival.upper_bound_seconds or 0)


def test_censored_prefix_is_retained_but_unknown_prefix_is_excluded() -> None:
    censored = _outcome(JourneyStatus.CENSORED)
    prefix = build_aft_row(
        censored,
        ready_at_utc=NOW,
        observation_horizon_utc=NOW + timedelta(seconds=600),
        base_query_weight=1,
        candidate_count=1,
    )
    assert prefix.included_in_likelihood
    assert prefix.lower_bound_seconds == 300
    excluded = build_aft_row(
        replace(censored, observation_complete_through_utc=None),
        ready_at_utc=NOW,
        observation_horizon_utc=NOW + timedelta(seconds=600),
        base_query_weight=1,
        candidate_count=1,
    )
    assert not excluded.included_in_likelihood
    assert excluded.exclusion_reason == "SOURCE_GAP"


def test_invalid_aft_rows_fail_instead_of_clipping() -> None:
    with pytest.raises(ValueError, match="0 < lower"):
        AftRow("key", 0, 1, 1, True, None)
    with pytest.raises(ValueError, match="both bounds"):
        AftRow("key", None, 1, 1, True, None)
    with pytest.raises(ValueError, match="requires a reason"):
        AftRow("key", None, None, 1, False, None)
    with pytest.raises(ValueError, match="candidate count"):
        build_aft_row(
            _outcome(JourneyStatus.ARRIVED),
            ready_at_utc=NOW,
            observation_horizon_utc=NOW + timedelta(seconds=1),
            base_query_weight=1,
            candidate_count=0,
        )
