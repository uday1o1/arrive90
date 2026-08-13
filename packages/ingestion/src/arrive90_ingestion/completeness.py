"""Conservative historical and prospective observation-window completeness rules."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from itertools import pairwise

from arrive90_data_contracts.realtime import CompletenessStatus, FreshnessStatus


class ReconciliationState(StrEnum):
    ARRIVED = "ARRIVED"
    CANCELED = "CANCELED"
    SKIPPED = "SKIPPED"
    SHORT_TURNED = "SHORT_TURNED"
    NON_REVENUE = "NON_REVENUE"
    DEPARTED_BEFORE_READY = "DEPARTED_BEFORE_READY"
    FULLY_OBSERVED_NO_ARRIVAL = "FULLY_OBSERVED_NO_ARRIVAL"
    AMBIGUOUS = "AMBIGUOUS"


TERMINAL_STATES = frozenset(
    state for state in ReconciliationState if state is not ReconciliationState.AMBIGUOUS
)


@dataclass(frozen=True)
class ReconciledTrain:
    identity: str
    state: ReconciliationState
    has_required_stop_intervals: bool
    source_row_keys: tuple[str, ...]


@dataclass(frozen=True)
class CompletenessResult:
    status: CompletenessStatus
    reason: str
    maximum_gap_seconds: int | None = None


def historical_completeness(
    expected_identities: set[str],
    trains: Iterable[ReconciledTrain],
    *,
    partition_quality_hash: str | None,
) -> CompletenessResult:
    """Require explicit per-train reconciliation instead of aggregate event density."""

    if partition_quality_hash is None:
        return CompletenessResult(CompletenessStatus.UNKNOWN, "PARTITION_QUALITY_UNKNOWN")
    train_list = list(trains)
    by_identity = {train.identity: train for train in train_list}
    if len(by_identity) != len(train_list):
        return CompletenessResult(CompletenessStatus.INCOMPLETE, "DUPLICATE_TRAIN_IDENTITY")
    if expected_identities - by_identity.keys():
        return CompletenessResult(CompletenessStatus.INCOMPLETE, "UNRECONCILED_ELIGIBLE_TRAIN")
    relevant = [by_identity[identity] for identity in expected_identities]
    if any(train.state not in TERMINAL_STATES for train in relevant):
        return CompletenessResult(CompletenessStatus.INCOMPLETE, "AMBIGUOUS_TRAIN_IDENTITY")
    if any(not train.has_required_stop_intervals for train in relevant):
        return CompletenessResult(CompletenessStatus.INCOMPLETE, "MISSING_RELEVANT_STOP_INTERVAL")
    if any(not train.source_row_keys for train in relevant):
        return CompletenessResult(CompletenessStatus.INCOMPLETE, "MISSING_SOURCE_LINEAGE")
    return CompletenessResult(CompletenessStatus.COMPLETE, "EVERY_ELIGIBLE_TRAIN_RECONCILED")


@dataclass(frozen=True)
class ProspectiveAttempt:
    scheduled_at_utc: datetime
    fetched_at_utc: datetime | None
    freshness: FreshnessStatus
    source_header_timestamp: datetime | None
    relevant_entity_observed: bool


def prospective_completeness(
    attempts: Iterable[ProspectiveAttempt], *, expected_cadence_seconds: int
) -> CompletenessResult:
    """Require every scheduled attempt, fresh coverage, monotonic headers, and route visibility."""

    if expected_cadence_seconds <= 0:
        raise ValueError("expected cadence must be positive")
    ordered = sorted(attempts, key=lambda attempt: attempt.scheduled_at_utc)
    if not ordered:
        return CompletenessResult(CompletenessStatus.UNKNOWN, "NO_SCHEDULED_ATTEMPTS")
    if any(attempt.fetched_at_utc is None for attempt in ordered):
        return CompletenessResult(CompletenessStatus.INCOMPLETE, "MISSING_FETCH_ATTEMPT")
    if any(attempt.freshness is not FreshnessStatus.FRESH for attempt in ordered):
        return CompletenessResult(CompletenessStatus.INCOMPLETE, "NON_FRESH_SNAPSHOT")
    if any(not attempt.relevant_entity_observed for attempt in ordered):
        return CompletenessResult(CompletenessStatus.INCOMPLETE, "RELEVANT_ENTITY_NOT_OBSERVED")
    headers = [attempt.source_header_timestamp for attempt in ordered]
    if any(header is None for header in headers):
        return CompletenessResult(CompletenessStatus.INCOMPLETE, "MISSING_SOURCE_HEADER")
    concrete_headers = [header for header in headers if header is not None]
    if concrete_headers != sorted(concrete_headers):
        return CompletenessResult(CompletenessStatus.INCOMPLETE, "REGRESSING_SOURCE_HEADER")
    fetched = [attempt.fetched_at_utc for attempt in ordered if attempt.fetched_at_utc is not None]
    maximum_gap = max(
        (int((later - earlier).total_seconds()) for earlier, later in pairwise(fetched)),
        default=0,
    )
    if maximum_gap > 2 * expected_cadence_seconds:
        return CompletenessResult(
            CompletenessStatus.INCOMPLETE, "EXCESSIVE_SOURCE_GAP", maximum_gap
        )
    return CompletenessResult(
        CompletenessStatus.COMPLETE, "PROSPECTIVE_COVERAGE_COMPLETE", maximum_gap
    )
