from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from arrive90_data_contracts.realtime import (
    FeedType,
    FetchAttempt,
    FreshnessStatus,
    ParseStatus,
    SemanticStatus,
    TransportStatus,
)
from arrive90_ingestion.collector import Collector, CollectorLimits
from arrive90_ingestion.storage import ImmutableAttemptStore
from google.transit import gtfs_realtime_pb2

NOW = datetime(2025, 1, 1, 12, tzinfo=UTC)


def _store(tmp_path: Path, *, quota: int = 10_000) -> ImmutableAttemptStore:
    return ImmutableAttemptStore(
        tmp_path / "store", daily_quota_bytes=quota, total_quota_bytes=quota
    )


def _feed(*, entities: int = 1, header_offset: int = 0, entity_after_header: bool = False) -> bytes:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = int((NOW + timedelta(seconds=header_offset)).timestamp())
    for index in range(entities):
        entity = feed.entity.add()
        entity.id = str(index)
        entity.vehicle.trip.trip_id = f"trip-{index}"
        entity.vehicle.timestamp = feed.header.timestamp + (1 if entity_after_header else 0)
    return cast(bytes, feed.SerializeToString())


def _ingest(collector: Collector, identifier: str, body: bytes | None) -> FetchAttempt:
    return collector.ingest(
        attempt_id=identifier,
        feed_type=FeedType.VEHICLE_POSITIONS,
        source_object="fixture",
        fetched_at_utc=NOW,
        body=body,
        http_status=200,
        transport_status=TransportStatus.SUCCEEDED,
    )


def test_valid_fresh_and_empty_feeds_remain_distinct(tmp_path: Path) -> None:
    collector = Collector(_store(tmp_path))
    valid = _ingest(collector, "valid", _feed())
    assert valid.parse_status is ParseStatus.VALID
    assert valid.semantic_status is SemanticStatus.VALID
    assert valid.freshness_status is FreshnessStatus.FRESH
    assert valid.maximum_entity_timestamp == NOW

    empty = _ingest(collector, "empty", _feed(entities=0))
    assert empty.parse_status is ParseStatus.EMPTY
    assert empty.semantic_status is SemanticStatus.VALID
    assert len(collector.store.blobs()) == 2


def test_malformed_missing_and_transport_failures_are_not_conflated(tmp_path: Path) -> None:
    collector = Collector(_store(tmp_path))
    malformed = _ingest(collector, "malformed", b"not protobuf")
    assert malformed.parse_status is ParseStatus.MALFORMED
    assert malformed.failure_code == "PROTOBUF_DECODE_ERROR"

    missing = _ingest(collector, "missing", None)
    assert missing.parse_status is ParseStatus.NOT_PARSED
    assert missing.failure_code == "MISSING_RESPONSE_BODY"

    failed = collector.ingest(
        attempt_id="failed",
        feed_type=FeedType.ALERTS,
        source_object="fixture",
        fetched_at_utc=NOW,
        body=None,
        http_status=503,
        transport_status=TransportStatus.FAILED,
    )
    assert failed.failure_code == "TRANSPORT_FAILURE"
    assert len(collector.store.attempts()) == 3


def test_payload_entity_and_required_field_limits_quarantine(tmp_path: Path) -> None:
    payload_collector = Collector(
        _store(tmp_path / "payload"), limits=CollectorLimits(maximum_payload_bytes=2)
    )
    oversized = _ingest(payload_collector, "oversized", b"123")
    assert oversized.failure_code == "PAYLOAD_TOO_LARGE"
    assert oversized.blob_sha256 is None

    entity_collector = Collector(
        _store(tmp_path / "entities"), limits=CollectorLimits(maximum_entities=1)
    )
    flooded = _ingest(entity_collector, "flooded", _feed(entities=2))
    assert flooded.failure_code == "ENTITY_LIMIT_EXCEEDED"
    assert flooded.semantic_status is SemanticStatus.QUARANTINED

    uninitialized = gtfs_realtime_pb2.FeedMessage().SerializePartialToString()
    missing_required = _ingest(entity_collector, "required", uninitialized)
    assert missing_required.failure_code == "MISSING_REQUIRED_FIELD"
    assert missing_required.semantic_status is SemanticStatus.INVALID


def test_timestamp_anomalies_and_freshness_states_are_explicit(tmp_path: Path) -> None:
    collector = Collector(_store(tmp_path))
    invalid_entity = _ingest(collector, "entity", _feed(entity_after_header=True))
    assert invalid_entity.failure_code == "ENTITY_TIMESTAMP_AFTER_HEADER"
    assert invalid_entity.semantic_status is SemanticStatus.INVALID

    clock_skew = _ingest(collector, "future", _feed(header_offset=31))
    assert clock_skew.freshness_status is FreshnessStatus.CLOCK_SKEW
    stale = _ingest(collector, "stale", _feed(header_offset=-91))
    assert stale.freshness_status is FreshnessStatus.STALE
    unusable = _ingest(collector, "unusable", _feed(header_offset=-301))
    assert unusable.freshness_status is FreshnessStatus.UNUSABLE


def test_parse_timeout_and_quota_exhaustion_are_retained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    collector = Collector(
        _store(tmp_path / "timeout"), limits=CollectorLimits(maximum_parse_seconds=1.0)
    )
    with monkeypatch.context() as scoped_patch:
        ticks = iter((0.0, 2.0))
        scoped_patch.setattr("arrive90_ingestion.collector.time.monotonic", lambda: next(ticks))
        timed_out = _ingest(collector, "timeout", _feed())
    assert timed_out.failure_code == "PARSE_TIMEOUT"

    first_body = _feed()
    quota_collector = Collector(_store(tmp_path / "quota", quota=len(first_body)))
    _ingest(quota_collector, "first", first_body)
    quota = _ingest(quota_collector, "second", _feed(header_offset=-1))
    assert quota.failure_code == "QUOTA_EXCEEDED"
    assert quota.blob_sha256 is None
    assert len(quota_collector.store.attempts()) == 2


def test_collector_limit_contract_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match="positive"):
        CollectorLimits(maximum_entities=0)
    with pytest.raises(ValueError, match="cannot precede"):
        CollectorLimits(fresh_seconds=100, hard_stale_seconds=99)
