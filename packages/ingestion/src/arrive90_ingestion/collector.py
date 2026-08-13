"""Bounded GTFS Realtime parsing with distinct attempt states and immutable lineage."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from arrive90_data_contracts.realtime import (
    FeedType,
    FetchAttempt,
    FreshnessStatus,
    ParseStatus,
    SemanticStatus,
    TransportStatus,
)
from google.protobuf.message import DecodeError  # type: ignore[import-untyped]
from google.transit import gtfs_realtime_pb2

from arrive90_ingestion.storage import ImmutableAttemptStore, QuotaExceededError


@dataclass(frozen=True)
class CollectorLimits:
    maximum_payload_bytes: int = 64 * 1024 * 1024
    maximum_entities: int = 500_000
    maximum_parse_seconds: float = 10.0
    fresh_seconds: int = 90
    hard_stale_seconds: int = 300
    future_clock_skew_seconds: int = 30

    def __post_init__(self) -> None:
        values = (
            self.maximum_payload_bytes,
            self.maximum_entities,
            self.maximum_parse_seconds,
            self.fresh_seconds,
            self.hard_stale_seconds,
        )
        if any(value <= 0 for value in values):
            raise ValueError("collector limits must be positive")
        if self.hard_stale_seconds < self.fresh_seconds:
            raise ValueError("hard stale cutoff cannot precede fresh cutoff")


class Collector:
    """Classify and persist one GTFS Realtime fetch without hiding failures."""

    def __init__(
        self,
        store: ImmutableAttemptStore,
        *,
        limits: CollectorLimits | None = None,
        parser_version: str = "gtfs-realtime-bindings-2.1.0",
        schema_version: str = "feed-attempt-v1",
    ) -> None:
        self.store = store
        self.limits = limits or CollectorLimits()
        self.parser_version = parser_version
        self.schema_version = schema_version

    @staticmethod
    def _timestamp(value: int | None) -> datetime | None:
        return datetime.fromtimestamp(value, tz=UTC) if value else None

    @staticmethod
    def _maximum_entity_timestamp(feed: gtfs_realtime_pb2.FeedMessage) -> datetime | None:
        values: list[int] = []
        for entity in feed.entity:
            if entity.HasField("vehicle") and entity.vehicle.HasField("timestamp"):
                values.append(entity.vehicle.timestamp)
            if entity.HasField("trip_update") and entity.trip_update.HasField("timestamp"):
                values.append(entity.trip_update.timestamp)
        return Collector._timestamp(max(values)) if values else None

    def _freshness(
        self, fetched_at: datetime, header: datetime | None
    ) -> tuple[int | None, FreshnessStatus]:
        if header is None:
            return None, FreshnessStatus.UNKNOWN
        age = int((fetched_at - header).total_seconds())
        if age < -self.limits.future_clock_skew_seconds:
            return age, FreshnessStatus.CLOCK_SKEW
        if age <= self.limits.fresh_seconds:
            return age, FreshnessStatus.FRESH
        if age <= self.limits.hard_stale_seconds:
            return age, FreshnessStatus.STALE
        return age, FreshnessStatus.UNUSABLE

    def ingest(
        self,
        *,
        attempt_id: str,
        feed_type: FeedType,
        source_object: str,
        fetched_at_utc: datetime,
        body: bytes | None,
        http_status: int | None,
        transport_status: TransportStatus,
        parent_attempt_id: str | None = None,
        content_type: str = "application/x-protobuf",
    ) -> FetchAttempt:
        """Parse, classify, and acknowledge one fetch attempt."""

        parse_status = ParseStatus.NOT_PARSED
        semantic_status = SemanticStatus.UNKNOWN
        freshness_status = FreshnessStatus.UNKNOWN
        failure_code: str | None = None
        header: datetime | None = None
        maximum_entity: datetime | None = None
        feed_age: int | None = None
        retained_body = body

        if transport_status is TransportStatus.SUCCEEDED and body is not None:
            if len(body) > self.limits.maximum_payload_bytes:
                retained_body = None
                semantic_status = SemanticStatus.QUARANTINED
                failure_code = "PAYLOAD_TOO_LARGE"
            else:
                started = time.monotonic()
                feed = gtfs_realtime_pb2.FeedMessage()
                try:
                    feed.ParseFromString(body)
                    elapsed = time.monotonic() - started
                    if elapsed > self.limits.maximum_parse_seconds:
                        parse_status = ParseStatus.MALFORMED
                        semantic_status = SemanticStatus.QUARANTINED
                        failure_code = "PARSE_TIMEOUT"
                    elif not feed.IsInitialized():
                        parse_status = ParseStatus.MALFORMED
                        semantic_status = SemanticStatus.INVALID
                        failure_code = "MISSING_REQUIRED_FIELD"
                    elif len(feed.entity) > self.limits.maximum_entities:
                        parse_status = ParseStatus.MALFORMED
                        semantic_status = SemanticStatus.QUARANTINED
                        failure_code = "ENTITY_LIMIT_EXCEEDED"
                    else:
                        parse_status = ParseStatus.EMPTY if not feed.entity else ParseStatus.VALID
                        semantic_status = SemanticStatus.VALID
                        header = self._timestamp(
                            feed.header.timestamp if feed.header.HasField("timestamp") else None
                        )
                        maximum_entity = self._maximum_entity_timestamp(feed)
                        feed_age, freshness_status = self._freshness(fetched_at_utc, header)
                        if (
                            maximum_entity is not None
                            and header is not None
                            and maximum_entity > header
                        ):
                            semantic_status = SemanticStatus.INVALID
                            failure_code = "ENTITY_TIMESTAMP_AFTER_HEADER"
                except DecodeError:
                    parse_status = ParseStatus.MALFORMED
                    semantic_status = SemanticStatus.QUARANTINED
                    failure_code = "PROTOBUF_DECODE_ERROR"
        elif transport_status is TransportStatus.SUCCEEDED:
            failure_code = "MISSING_RESPONSE_BODY"
        else:
            failure_code = "TRANSPORT_FAILURE"

        attempt = FetchAttempt(
            attempt_id=attempt_id,
            parent_attempt_id=parent_attempt_id,
            agency_id="mbta",
            feed_type=feed_type,
            source_object=source_object,
            fetched_at_utc=fetched_at_utc,
            source_header_timestamp=header,
            maximum_entity_timestamp=maximum_entity,
            http_status=http_status,
            blob_sha256=hashlib.sha256(retained_body).hexdigest()
            if retained_body is not None
            else None,
            parser_version=self.parser_version,
            schema_version=self.schema_version,
            feed_age_seconds=feed_age,
            transport_status=transport_status,
            parse_status=parse_status,
            semantic_status=semantic_status,
            freshness_status=freshness_status,
            failure_code=failure_code,
        )
        try:
            self.store.record(attempt, retained_body, content_type)
        except QuotaExceededError:
            quota_attempt = replace(
                attempt,
                blob_sha256=None,
                parse_status=ParseStatus.NOT_PARSED,
                semantic_status=SemanticStatus.QUARANTINED,
                freshness_status=FreshnessStatus.UNKNOWN,
                failure_code="QUOTA_EXCEEDED",
            )
            self.store.record(quota_attempt, None, content_type)
            return quota_attempt
        return attempt
