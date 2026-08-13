"""Versioned immutable source and realtime attempt contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum


class FeedType(StrEnum):
    STATIC = "STATIC"
    TRIP_UPDATES = "TRIP_UPDATES"
    VEHICLE_POSITIONS = "VEHICLE_POSITIONS"
    ALERTS = "ALERTS"


class TransportStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"


class ParseStatus(StrEnum):
    VALID = "VALID"
    EMPTY = "EMPTY"
    MALFORMED = "MALFORMED"
    NOT_PARSED = "NOT_PARSED"


class SemanticStatus(StrEnum):
    VALID = "VALID"
    INVALID = "INVALID"
    QUARANTINED = "QUARANTINED"
    UNKNOWN = "UNKNOWN"


class FreshnessStatus(StrEnum):
    FRESH = "FRESH"
    STALE = "STALE"
    UNUSABLE = "UNUSABLE"
    CLOCK_SKEW = "CLOCK_SKEW"
    UNKNOWN = "UNKNOWN"


class SourceKind(StrEnum):
    LAMP_SUBWAY = "LAMP_SUBWAY"
    LAMP_ALERTS = "LAMP_ALERTS"
    GTFS_ARCHIVE = "GTFS_ARCHIVE"


class CompletenessStatus(StrEnum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    UNKNOWN = "UNKNOWN"


def require_utc(value: datetime, field: str) -> None:
    """Reject naive and non-UTC timestamps at storage boundaries."""

    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{field} must be timezone-aware UTC")


@dataclass(frozen=True)
class FeedBlob:
    blob_sha256: str
    content_type: str
    content_length: int
    storage_uri: str
    first_seen_at_utc: datetime

    def __post_init__(self) -> None:
        if len(self.blob_sha256) != 64 or any(
            c not in "0123456789abcdef" for c in self.blob_sha256
        ):
            raise ValueError("blob_sha256 must be lowercase hexadecimal SHA-256")
        if self.content_length < 0:
            raise ValueError("content_length cannot be negative")
        require_utc(self.first_seen_at_utc, "first_seen_at_utc")


@dataclass(frozen=True)
class FetchAttempt:
    attempt_id: str
    parent_attempt_id: str | None
    agency_id: str
    feed_type: FeedType
    source_object: str
    fetched_at_utc: datetime
    source_header_timestamp: datetime | None
    maximum_entity_timestamp: datetime | None
    http_status: int | None
    blob_sha256: str | None
    parser_version: str
    schema_version: str
    feed_age_seconds: int | None
    transport_status: TransportStatus
    parse_status: ParseStatus
    semantic_status: SemanticStatus
    freshness_status: FreshnessStatus
    failure_code: str | None = None

    def __post_init__(self) -> None:
        require_utc(self.fetched_at_utc, "fetched_at_utc")
        if self.source_header_timestamp is not None:
            require_utc(self.source_header_timestamp, "source_header_timestamp")
        if self.maximum_entity_timestamp is not None:
            require_utc(self.maximum_entity_timestamp, "maximum_entity_timestamp")
        if self.blob_sha256 is not None and len(self.blob_sha256) != 64:
            raise ValueError("blob_sha256 must be a SHA-256 digest")


@dataclass(frozen=True)
class HistoricalSourceObject:
    source_object_id: str
    source_kind: SourceKind
    source_uri: str
    published_or_listed_at_utc: datetime | None
    downloaded_at_utc: datetime
    blob_sha256: str
    schema_fingerprint: str
    parser_version: str

    def __post_init__(self) -> None:
        require_utc(self.downloaded_at_utc, "downloaded_at_utc")
        if self.published_or_listed_at_utc is not None:
            require_utc(self.published_or_listed_at_utc, "published_or_listed_at_utc")


@dataclass(frozen=True)
class ObservationCoverageWindow:
    source_track: str
    route_id: str
    direction_id: int
    window_start_utc: datetime
    window_end_utc: datetime
    completeness_status: CompletenessStatus
    completeness_reason: str
    expected_cadence_seconds: int | None = None
    maximum_observed_gap_seconds: int | None = None

    def __post_init__(self) -> None:
        require_utc(self.window_start_utc, "window_start_utc")
        require_utc(self.window_end_utc, "window_end_utc")
        if self.window_end_utc <= self.window_start_utc:
            raise ValueError("coverage window end must follow start")
