from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from arrive90_data_contracts.realtime import CompletenessStatus, FreshnessStatus
from arrive90_ingestion.completeness import (
    ProspectiveAttempt,
    ReconciledTrain,
    ReconciliationState,
    historical_completeness,
    prospective_completeness,
)

NOW = datetime(2025, 1, 1, tzinfo=UTC)


def _train(**changes: object) -> ReconciledTrain:
    values: dict[str, object] = {
        "identity": "train",
        "state": ReconciliationState.ARRIVED,
        "has_required_stop_intervals": True,
        "source_row_keys": ("row",),
    }
    values.update(changes)
    return ReconciledTrain(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("trains", "quality", "reason"),
    [
        ([_train()], None, "PARTITION_QUALITY_UNKNOWN"),
        ([], "hash", "UNRECONCILED_ELIGIBLE_TRAIN"),
        ([_train(), _train()], "hash", "DUPLICATE_TRAIN_IDENTITY"),
        ([_train(state=ReconciliationState.AMBIGUOUS)], "hash", "AMBIGUOUS_TRAIN_IDENTITY"),
        ([_train(has_required_stop_intervals=False)], "hash", "MISSING_RELEVANT_STOP_INTERVAL"),
        ([_train(source_row_keys=())], "hash", "MISSING_SOURCE_LINEAGE"),
    ],
)
def test_historical_completeness_fails_closed(
    trains: list[ReconciledTrain], quality: str | None, reason: str
) -> None:
    result = historical_completeness({"train"}, trains, partition_quality_hash=quality)
    assert result.status is not CompletenessStatus.COMPLETE
    assert result.reason == reason


def test_historical_completeness_requires_explicit_terminal_reconciliation() -> None:
    result = historical_completeness({"train"}, [_train()], partition_quality_hash="hash")
    assert result.status is CompletenessStatus.COMPLETE


def _attempt(index: int, **changes: object) -> ProspectiveAttempt:
    timestamp = NOW + timedelta(seconds=30 * index)
    values: dict[str, object] = {
        "scheduled_at_utc": timestamp,
        "fetched_at_utc": timestamp,
        "freshness": FreshnessStatus.FRESH,
        "source_header_timestamp": timestamp,
        "relevant_entity_observed": True,
    }
    values.update(changes)
    return ProspectiveAttempt(**values)  # type: ignore[arg-type]


def test_prospective_completeness_passes_bounded_monotonic_sequence() -> None:
    result = prospective_completeness([_attempt(0), _attempt(1)], expected_cadence_seconds=30)
    assert result.status is CompletenessStatus.COMPLETE
    assert result.maximum_gap_seconds == 30


@pytest.mark.parametrize(
    ("attempts", "reason"),
    [
        ([], "NO_SCHEDULED_ATTEMPTS"),
        ([_attempt(0, fetched_at_utc=None)], "MISSING_FETCH_ATTEMPT"),
        ([_attempt(0, freshness=FreshnessStatus.STALE)], "NON_FRESH_SNAPSHOT"),
        ([_attempt(0, relevant_entity_observed=False)], "RELEVANT_ENTITY_NOT_OBSERVED"),
        ([_attempt(0, source_header_timestamp=None)], "MISSING_SOURCE_HEADER"),
        (
            [
                _attempt(0, source_header_timestamp=NOW + timedelta(seconds=1)),
                _attempt(1, source_header_timestamp=NOW),
            ],
            "REGRESSING_SOURCE_HEADER",
        ),
        (
            [_attempt(0), _attempt(1, fetched_at_utc=NOW + timedelta(seconds=61))],
            "EXCESSIVE_SOURCE_GAP",
        ),
    ],
)
def test_prospective_completeness_failure_reasons(
    attempts: list[ProspectiveAttempt], reason: str
) -> None:
    result = prospective_completeness(attempts, expected_cadence_seconds=30)
    assert result.status is not CompletenessStatus.COMPLETE
    assert result.reason == reason


def test_prospective_cadence_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        prospective_completeness([], expected_cadence_seconds=0)
