from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from arrive90_data_contracts.realtime import (
    FeedType,
    FetchAttempt,
    FreshnessStatus,
    ParseStatus,
    SemanticStatus,
    TransportStatus,
)
from arrive90_ingestion.storage import ImmutableAttemptStore, QuotaExceededError

NOW = datetime(2025, 1, 1, tzinfo=UTC)


def _attempt(identifier: str, body: bytes | None, fetched_at: datetime = NOW) -> FetchAttempt:
    return FetchAttempt(
        attempt_id=identifier,
        parent_attempt_id=None,
        agency_id="mbta",
        feed_type=FeedType.VEHICLE_POSITIONS,
        source_object="fixture",
        fetched_at_utc=fetched_at,
        source_header_timestamp=NOW,
        maximum_entity_timestamp=None,
        http_status=200,
        blob_sha256=hashlib.sha256(body).hexdigest() if body is not None else None,
        parser_version="fixture",
        schema_version="v1",
        feed_age_seconds=0,
        transport_status=TransportStatus.SUCCEEDED,
        parse_status=ParseStatus.VALID,
        semantic_status=SemanticStatus.VALID,
        freshness_status=FreshnessStatus.FRESH,
    )


def test_identical_bytes_deduplicate_blob_but_retain_attempts_and_restart(tmp_path: Path) -> None:
    body = b"same immutable bytes"
    store = ImmutableAttemptStore(tmp_path / "store", daily_quota_bytes=100, total_quota_bytes=100)
    first = store.record(_attempt("a", body), body, "application/x-protobuf")
    second = store.record(_attempt("b", body), body, "application/x-protobuf")
    assert first == second
    assert len(store.blobs()) == 1
    assert [row["attempt_id"] for row in store.attempts()] == ["a", "b"]
    restarted = ImmutableAttemptStore(
        tmp_path / "store", daily_quota_bytes=100, total_quota_bytes=100
    )
    assert len(restarted.blobs()) == 1
    assert len(restarted.attempts()) == 2


def test_body_digest_must_match_and_attempt_ids_are_immutable(tmp_path: Path) -> None:
    store = ImmutableAttemptStore(tmp_path / "store", daily_quota_bytes=100, total_quota_bytes=100)
    body = b"body"
    with pytest.raises(ValueError, match="does not match"):
        store.record(_attempt("a", b"different"), body, "type")
    store.record(_attempt("a", body), body, "type")
    with pytest.raises(sqlite3.IntegrityError, match="already exists"):
        store.record(_attempt("a", body), body, "type")


def test_daily_and_total_quotas_apply_only_to_new_content(tmp_path: Path) -> None:
    daily_store = ImmutableAttemptStore(
        tmp_path / "daily", daily_quota_bytes=4, total_quota_bytes=100
    )
    daily_store.record(_attempt("a", b"1234"), b"1234", "type")
    daily_store.record(_attempt("b", b"1234"), b"1234", "type")
    with pytest.raises(QuotaExceededError, match="daily"):
        daily_store.record(_attempt("c", b"5"), b"5", "type")

    total_store = ImmutableAttemptStore(
        tmp_path / "total", daily_quota_bytes=100, total_quota_bytes=4
    )
    total_store.record(_attempt("a", b"1234"), b"1234", "type")
    with pytest.raises(QuotaExceededError, match="total"):
        total_store.record(_attempt("b", b"5", NOW + timedelta(days=1)), b"5", "type")


def test_store_rejects_nonpositive_quotas_and_records_bodyless_attempt(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="positive"):
        ImmutableAttemptStore(tmp_path / "bad", daily_quota_bytes=0, total_quota_bytes=1)
    store = ImmutableAttemptStore(tmp_path / "store", daily_quota_bytes=1, total_quota_bytes=1)
    assert store.record(_attempt("missing", None), None, "type") is None
    assert store.attempts()[0]["blob_sha256"] is None
