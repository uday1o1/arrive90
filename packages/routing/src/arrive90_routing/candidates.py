"""Candidate normalization, deduplication, and schedule feature derivation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass

from arrive90_data_contracts.candidates import CandidateItinerary


def candidate_order(candidate: CandidateItinerary) -> tuple[object, ...]:
    """Return the frozen outcome-independent candidate truncation order."""

    return (
        candidate.scheduled_arrival_utc,
        candidate.scheduled_departure_utc,
        candidate.transfer_count,
        candidate.route_pattern_tuple,
        candidate.platform_stop_tuple,
        candidate.policy_key.encode(),
    )


def _representative_order(candidate: CandidateItinerary) -> tuple[object, ...]:
    return (
        *candidate_order(candidate),
        tuple(leg.trip_id.encode() for leg in candidate.legs),
    )


def deduplicate_and_limit(
    candidates: Iterable[CandidateItinerary], *, maximum_alternatives: int = 16
) -> tuple[CandidateItinerary, ...]:
    """Select one canonical departure per policy and apply the shared cap."""

    if maximum_alternatives <= 0:
        raise ValueError("maximum alternatives must be positive")
    by_policy: dict[str, CandidateItinerary] = {}
    for candidate in candidates:
        prior = by_policy.get(candidate.policy_key)
        if prior is None or _representative_order(candidate) < _representative_order(prior):
            by_policy[candidate.policy_key] = candidate
    return tuple(sorted(by_policy.values(), key=candidate_order)[:maximum_alternatives])


def eligible_trip_set_hash(candidates: Iterable[CandidateItinerary]) -> str:
    """Hash exact candidate and trip lineage without depending on input order."""

    rows = sorted(
        (
            candidate.policy_key,
            tuple(leg.trip_id for leg in candidate.legs),
            candidate.scheduled_departure_utc.isoformat(),
            candidate.scheduled_arrival_utc.isoformat(),
        )
        for candidate in candidates
    )
    payload = json.dumps(rows, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class CanonicalScheduleFeatures:
    policy_key: str
    scheduled_departure_epoch_seconds: int
    scheduled_arrival_epoch_seconds: int
    scheduled_duration_seconds: int
    transfer_count: int
    transfer_buffer_seconds: int | None
    route_pattern_tuple: tuple[str, ...]
    platform_stop_tuple: tuple[str, ...]


def canonical_schedule_features(candidate: CandidateItinerary) -> CanonicalScheduleFeatures:
    transfer_buffer: int | None = None
    if candidate.transfer_count:
        transfer_buffer = int(
            (
                candidate.legs[1].scheduled_departure_utc - candidate.legs[0].scheduled_arrival_utc
            ).total_seconds()
        )
    return CanonicalScheduleFeatures(
        policy_key=candidate.policy_key,
        scheduled_departure_epoch_seconds=int(candidate.scheduled_departure_utc.timestamp()),
        scheduled_arrival_epoch_seconds=int(candidate.scheduled_arrival_utc.timestamp()),
        scheduled_duration_seconds=candidate.planned_duration_seconds,
        transfer_count=candidate.transfer_count,
        transfer_buffer_seconds=transfer_buffer,
        route_pattern_tuple=candidate.route_pattern_tuple,
        platform_stop_tuple=candidate.platform_stop_tuple,
    )
