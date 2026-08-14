"""Server-owned initial cutoff and conservative time normalization."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from arrive90_data_contracts.realtime import require_utc

from arrive90_service.contracts import NormalizedJourneyRequest


def normalize_initial_request(
    *,
    origin_station_id: str,
    destination_station_id: str,
    requested_ready_at_utc: datetime,
    requested_deadline_at_utc: datetime,
    reliability_target: Decimal,
    maximum_extra_minutes: int,
    initial_query_cutoff_utc: datetime,
    supported_station_ids: frozenset[str],
) -> NormalizedJourneyRequest:
    require_utc(requested_ready_at_utc, "ready_at")
    require_utc(requested_deadline_at_utc, "deadline")
    require_utc(initial_query_cutoff_utc, "initial_query_cutoff_utc")
    if origin_station_id == destination_station_id:
        raise ValueError("origin and destination must be distinct")
    if {origin_station_id, destination_station_id} - supported_station_ids:
        raise ValueError("station is outside the supported scope")
    if reliability_target not in {Decimal("0.80"), Decimal("0.90"), Decimal("0.95")}:
        raise ValueError("reliability target is outside the supported set")
    if not 0 <= maximum_extra_minutes <= 20:
        raise ValueError("maximum extra time must be from zero through 20 minutes")
    ready_delta = requested_ready_at_utc - initial_query_cutoff_utc
    if ready_delta < timedelta(minutes=-2) or ready_delta > timedelta(hours=24):
        raise ValueError("ready time is outside the accepted range")
    if requested_ready_at_utc < initial_query_cutoff_utc:
        effective_ready = initial_query_cutoff_utc
        ready_status = "NORMALIZED_TO_CUTOFF"
        limitations = ["READY_TIME_NORMALIZED_TO_CUTOFF"]
    else:
        effective_ready = requested_ready_at_utc
        ready_status = "AS_REQUESTED"
        limitations = []
    raw_slack_seconds = int((requested_deadline_at_utc - effective_ready).total_seconds())
    if not 300 <= raw_slack_seconds <= 10_800:
        raise ValueError("deadline is outside the accepted range")
    effective_slack_seconds = (raw_slack_seconds // 300) * 300
    effective_deadline = effective_ready + timedelta(seconds=effective_slack_seconds)
    if effective_deadline != requested_deadline_at_utc:
        deadline_status = "NORMALIZED_DOWN_TO_SUPPORTED_GRID"
        limitations.append("DEADLINE_NORMALIZED_DOWN_TO_SUPPORTED_GRID")
    else:
        deadline_status = "AS_REQUESTED"
    return NormalizedJourneyRequest(
        origin_station_id,
        destination_station_id,
        requested_ready_at_utc,
        effective_ready,
        requested_deadline_at_utc,
        effective_deadline,
        reliability_target,
        maximum_extra_minutes,
        initial_query_cutoff_utc,
        ready_status,
        deadline_status,
        tuple(limitations),
    )
