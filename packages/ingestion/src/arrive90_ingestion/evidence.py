"""Normalize primitive stop observations without concealing evidence provenance."""

from __future__ import annotations

from collections.abc import Iterable

from arrive90_data_contracts.realtime import FeedType
from arrive90_data_contracts.schedule import (
    ArrivalEvidence,
    DepartureEvidence,
    IntervalClosure,
    NormalizedStopEvidence,
    PrimitiveStopObservation,
    VehicleStatus,
)


def normalize_stop_observations(
    observations: Iterable[PrimitiveStopObservation],
) -> tuple[NormalizedStopEvidence, ...]:
    """Preserve direct, conservative, and Trip Update evidence as separate classes."""

    ordered = sorted(
        observations,
        key=lambda observation: (
            observation.observed_trip_id,
            observation.event_time_utc,
            observation.source_row_key,
        ),
    )
    output: list[NormalizedStopEvidence] = []
    for observation in ordered:
        if (
            observation.feed_type is FeedType.VEHICLE_POSITIONS
            and observation.vehicle_status is VehicleStatus.STOPPED_AT
        ):
            output.append(
                NormalizedStopEvidence(
                    source_row_key=observation.source_row_key,
                    observed_trip_id=observation.observed_trip_id,
                    stop_id=observation.stop_id,
                    stop_sequence=observation.stop_sequence,
                    arrival_lower_bound_utc=observation.event_time_utc,
                    arrival_upper_bound_utc=observation.event_time_utc,
                    arrival_interval_closed=IntervalClosure.EXACT,
                    arrival_evidence=ArrivalEvidence.VP_STOPPED_AT,
                    departure_upper_bound_utc=None,
                    departure_evidence=DepartureEvidence.UNKNOWN,
                    product_available_at_utc=observation.product_available_at_utc,
                    usable_for_primary_boarding=True,
                )
            )
            continue
        if (
            observation.feed_type is FeedType.VEHICLE_POSITIONS
            and observation.vehicle_status is VehicleStatus.IN_TRANSIT_TO
            and observation.previous_stop_id is not None
        ):
            output.append(
                NormalizedStopEvidence(
                    source_row_key=observation.source_row_key,
                    observed_trip_id=observation.observed_trip_id,
                    stop_id=observation.previous_stop_id,
                    stop_sequence=max(0, observation.stop_sequence - 1),
                    arrival_lower_bound_utc=None,
                    arrival_upper_bound_utc=observation.event_time_utc,
                    arrival_interval_closed=IntervalClosure.LEFT_OPEN_RIGHT_CLOSED,
                    arrival_evidence=ArrivalEvidence.VP_DEPARTED_STATION_UPPER_BOUND,
                    departure_upper_bound_utc=observation.event_time_utc,
                    departure_evidence=DepartureEvidence.DOWNSTREAM_MOVE_UPPER_BOUND,
                    product_available_at_utc=observation.product_available_at_utc,
                    usable_for_primary_boarding=False,
                )
            )
            continue
        if observation.feed_type is FeedType.TRIP_UPDATES:
            evidence = (
                ArrivalEvidence.PREDICTED_TRIP_UPDATE
                if observation.is_prediction
                else ArrivalEvidence.VERIFIED_PAST_TRIP_UPDATE
            )
            output.append(
                NormalizedStopEvidence(
                    source_row_key=observation.source_row_key,
                    observed_trip_id=observation.observed_trip_id,
                    stop_id=observation.stop_id,
                    stop_sequence=observation.stop_sequence,
                    arrival_lower_bound_utc=observation.event_time_utc,
                    arrival_upper_bound_utc=observation.event_time_utc,
                    arrival_interval_closed=IntervalClosure.EXACT,
                    arrival_evidence=evidence,
                    departure_upper_bound_utc=None,
                    departure_evidence=DepartureEvidence.UNKNOWN,
                    product_available_at_utc=observation.product_available_at_utc,
                    usable_for_primary_boarding=False,
                )
            )
    return tuple(output)
