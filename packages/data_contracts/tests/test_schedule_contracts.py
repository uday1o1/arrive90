from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest
from arrive90_data_contracts.realtime import FeedType
from arrive90_data_contracts.schedule import (
    AlertEffect,
    AlertRevision,
    ArrivalEvidence,
    DepartureEvidence,
    IntervalClosure,
    NormalizedStopEvidence,
    PrimitiveStopObservation,
    ScheduleStopTime,
    VehicleStatus,
)

NOW = datetime(2025, 1, 1, tzinfo=UTC)


def _schedule() -> ScheduleStopTime:
    return ScheduleStopTime(
        "version",
        "feed",
        NOW,
        NOW + timedelta(minutes=1),
        date(2025, 1, 1),
        date(2025, 1, 31),
        date(2025, 1, 2),
        "weekday",
        "Red",
        0,
        "trip",
        None,
        "stop",
        "station",
        1,
        25 * 3600,
        25 * 3600 + 30,
        0,
        0,
        1,
    )


def test_schedule_contract_preserves_gtfs_times_beyond_midnight() -> None:
    row = _schedule()
    assert row.scheduled_arrival_local_seconds == 90_000
    with pytest.raises(ValueError, match="known before"):
        replace(row, known_at_utc=NOW - timedelta(seconds=1))
    with pytest.raises(ValueError, match="active interval"):
        replace(row, service_date=date(2025, 2, 1))
    with pytest.raises(ValueError, match="cannot precede"):
        replace(row, scheduled_departure_local_seconds=89_999)


def test_primitive_observation_rejects_noncausal_temporal_lineage() -> None:
    observation = PrimitiveStopObservation(
        "row",
        FeedType.VEHICLE_POSITIONS,
        "trip",
        "Red",
        0,
        "stop",
        "station",
        1,
        VehicleStatus.STOPPED_AT,
        NOW,
        NOW,
        NOW,
        NOW,
    )
    assert observation.vehicle_status is VehicleStatus.STOPPED_AT
    with pytest.raises(ValueError, match="not monotonic"):
        replace(observation, product_available_at_utc=NOW - timedelta(seconds=1))


def test_normalized_evidence_only_allows_direct_stop_boarding() -> None:
    evidence = NormalizedStopEvidence(
        "row",
        "trip",
        "stop",
        1,
        NOW,
        NOW,
        IntervalClosure.EXACT,
        ArrivalEvidence.VP_STOPPED_AT,
        None,
        DepartureEvidence.UNKNOWN,
        NOW,
        True,
    )
    assert evidence.usable_for_primary_boarding
    with pytest.raises(ValueError, match="only direct"):
        replace(
            evidence,
            arrival_evidence=ArrivalEvidence.PREDICTED_TRIP_UPDATE,
        )


def test_alert_revision_contract_rejects_invalid_intervals_and_hashes() -> None:
    revision = AlertRevision(
        "alert",
        1,
        "attempt",
        NOW,
        NOW,
        NOW,
        NOW + timedelta(hours=1),
        ("route:Red",),
        AlertEffect.NO_SERVICE,
        "a" * 64,
    )
    assert revision.effect is AlertEffect.NO_SERVICE
    with pytest.raises(ValueError, match="positive"):
        replace(revision, revision_number=0)
    with pytest.raises(ValueError, match="increasing"):
        replace(revision, active_end_utc=NOW)
    with pytest.raises(ValueError, match="unique and sorted"):
        replace(revision, informed_entity_keys=("z", "a"))
    with pytest.raises(ValueError, match="text_sha256"):
        replace(revision, text_sha256="short")
