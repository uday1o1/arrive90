from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from arrive90_data_contracts.realtime import FeedType
from arrive90_data_contracts.schedule import (
    ArrivalEvidence,
    DepartureEvidence,
    PrimitiveStopObservation,
    VehicleStatus,
)
from arrive90_ingestion.evidence import normalize_stop_observations

NOW = datetime(2025, 1, 1, tzinfo=UTC)


def _observation() -> PrimitiveStopObservation:
    return PrimitiveStopObservation(
        "direct",
        FeedType.VEHICLE_POSITIONS,
        "trip",
        "Red",
        0,
        "stop-a",
        "station-a",
        1,
        VehicleStatus.STOPPED_AT,
        NOW,
        NOW,
        NOW,
        NOW,
    )


def test_direct_conservative_and_trip_update_evidence_remain_distinct() -> None:
    direct = _observation()
    move = replace(
        direct,
        source_row_key="move",
        stop_id="stop-b",
        stop_sequence=2,
        vehicle_status=VehicleStatus.IN_TRANSIT_TO,
        event_time_utc=NOW + timedelta(seconds=20),
        source_observed_at_utc=NOW + timedelta(seconds=20),
        pipeline_known_at_utc=NOW + timedelta(seconds=20),
        product_available_at_utc=NOW + timedelta(seconds=20),
        previous_stop_id="stop-a",
    )
    predicted = replace(
        direct,
        source_row_key="prediction",
        feed_type=FeedType.TRIP_UPDATES,
        is_prediction=True,
    )
    verified = replace(predicted, source_row_key="verified", is_prediction=False)
    result = normalize_stop_observations((predicted, move, direct, verified))
    by_key = {item.source_row_key: item for item in result}
    assert by_key["direct"].arrival_evidence is ArrivalEvidence.VP_STOPPED_AT
    assert by_key["direct"].usable_for_primary_boarding
    assert by_key["move"].arrival_evidence is ArrivalEvidence.VP_DEPARTED_STATION_UPPER_BOUND
    assert by_key["move"].departure_evidence is DepartureEvidence.DOWNSTREAM_MOVE_UPPER_BOUND
    assert not by_key["move"].usable_for_primary_boarding
    assert by_key["prediction"].arrival_evidence is ArrivalEvidence.PREDICTED_TRIP_UPDATE
    assert by_key["verified"].arrival_evidence is ArrivalEvidence.VERIFIED_PAST_TRIP_UPDATE


def test_irrelevant_primitive_is_not_silently_promoted() -> None:
    unknown = replace(_observation(), vehicle_status=VehicleStatus.UNKNOWN)
    assert normalize_stop_observations((unknown,)) == ()
