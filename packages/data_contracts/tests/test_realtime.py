from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from arrive90_data_contracts.realtime import (
    CompletenessStatus,
    FeedBlob,
    FeedType,
    FetchAttempt,
    FreshnessStatus,
    HistoricalSourceObject,
    ObservationCoverageWindow,
    ParseStatus,
    SemanticStatus,
    SourceKind,
    TransportStatus,
)

NOW = datetime(2025, 1, 1, tzinfo=UTC)


def test_feed_blob_validates_digest_length_size_and_utc() -> None:
    blob = FeedBlob("a" * 64, "application/octet-stream", 1, "file:///blob", NOW)
    assert blob.content_length == 1
    with pytest.raises(ValueError, match="lowercase hexadecimal"):
        FeedBlob("A" * 64, "type", 1, "file:///blob", NOW)
    with pytest.raises(ValueError, match="cannot be negative"):
        FeedBlob("a" * 64, "type", -1, "file:///blob", NOW)
    with pytest.raises(ValueError, match="timezone-aware UTC"):
        FeedBlob("a" * 64, "type", 1, "file:///blob", NOW.replace(tzinfo=None))


def test_fetch_attempt_validates_optional_timestamps_and_digest() -> None:
    attempt = FetchAttempt(
        "attempt",
        None,
        "mbta",
        FeedType.ALERTS,
        "source",
        NOW,
        NOW,
        NOW,
        200,
        "b" * 64,
        "parser",
        "d" * 64,
        0,
        TransportStatus.SUCCEEDED,
        ParseStatus.VALID,
        SemanticStatus.VALID,
        FreshnessStatus.FRESH,
    )
    assert attempt.feed_type is FeedType.ALERTS
    with pytest.raises(ValueError, match="SHA-256"):
        FetchAttempt(**{**attempt.__dict__, "blob_sha256": "short"})
    with pytest.raises(ValueError, match="source_header_timestamp"):
        FetchAttempt(**{**attempt.__dict__, "source_header_timestamp": NOW.replace(tzinfo=None)})
    with pytest.raises(ValueError, match="maximum_entity_timestamp"):
        FetchAttempt(**{**attempt.__dict__, "maximum_entity_timestamp": NOW.replace(tzinfo=None)})


def test_historical_source_and_coverage_window_validate_time_contracts() -> None:
    source = HistoricalSourceObject(
        "source",
        SourceKind.LAMP_SUBWAY,
        "https://example.invalid",
        NOW,
        NOW + timedelta(days=1),
        "c" * 64,
        "d" * 64,
        "parser",
    )
    assert source.source_kind is SourceKind.LAMP_SUBWAY
    with pytest.raises(ValueError, match="published_or_listed"):
        HistoricalSourceObject(
            **{**source.__dict__, "published_or_listed_at_utc": NOW.replace(tzinfo=None)}
        )
    with pytest.raises(ValueError, match="downloaded before"):
        HistoricalSourceObject(
            **{
                **source.__dict__,
                "published_or_listed_at_utc": NOW + timedelta(days=2),
            }
        )
    with pytest.raises(ValueError, match="blob_sha256"):
        HistoricalSourceObject(**{**source.__dict__, "blob_sha256": "short"})
    with pytest.raises(ValueError, match="schema_fingerprint"):
        HistoricalSourceObject(**{**source.__dict__, "schema_fingerprint": "short"})
    window = ObservationCoverageWindow(
        "HISTORICAL_LAMP",
        "Red",
        0,
        NOW,
        NOW + timedelta(hours=1),
        CompletenessStatus.COMPLETE,
        "fixture",
    )
    assert window.completeness_status is CompletenessStatus.COMPLETE
    with pytest.raises(ValueError, match="end must follow start"):
        ObservationCoverageWindow(**{**window.__dict__, "window_end_utc": NOW})
