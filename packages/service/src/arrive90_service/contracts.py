"""Typed service boundaries independent of FastAPI request parsing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from arrive90_data_contracts.candidates import CandidateItinerary
from arrive90_data_contracts.realtime import require_utc
from arrive90_decision.contracts import (
    CandidateScore,
    DecisionContext,
    EligibilityManifest,
    HorizonSupportManifest,
    RecoveryTriggerInput,
    ScoringState,
)


class FeedStatus(StrEnum):
    FRESH = "FRESH"
    STALE = "STALE"
    ABSENT = "ABSENT"


class LiveEventKind(StrEnum):
    FEED_FRESHNESS_CHANGED = "FEED_FRESHNESS_CHANGED"
    OFFICIAL_TRIP_UPDATE = "OFFICIAL_TRIP_UPDATE"
    ALERT_ELIGIBILITY_CHANGED = "ALERT_ELIGIBILITY_CHANGED"
    ORIGINAL_POLICY_UNSUPPORTED = "ORIGINAL_POLICY_UNSUPPORTED"
    CONDITIONAL_TRANSFER_ESTIMATE = "CONDITIONAL_TRANSFER_ESTIMATE"


@dataclass(frozen=True)
class Station:
    station_id: str
    name: str


@dataclass(frozen=True)
class NormalizedJourneyRequest:
    origin_station_id: str
    destination_station_id: str
    requested_ready_at_utc: datetime
    effective_ready_at_utc: datetime
    requested_deadline_at_utc: datetime
    effective_deadline_at_utc: datetime
    reliability_target: Decimal
    maximum_extra_minutes: int
    initial_query_cutoff_utc: datetime
    ready_time_status: str
    deadline_time_status: str
    limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in (
            "requested_ready_at_utc",
            "effective_ready_at_utc",
            "requested_deadline_at_utc",
            "effective_deadline_at_utc",
            "initial_query_cutoff_utc",
        ):
            require_utc(getattr(self, name), name)


@dataclass(frozen=True)
class SearchMaterials:
    scores: tuple[CandidateScore, ...]
    decision_context: DecisionContext
    eligibility_manifest: EligibilityManifest
    horizon_support_manifest: HorizonSupportManifest
    scoring_state: ScoringState
    feed_status: FeedStatus
    model_version: str
    feature_schema_version: str
    candidate_generator_version: str = "STATIC_ROUTE_POLICY_V1"
    source_attempt_lineage: tuple[str, ...] = ()


class JourneyBackend(Protocol):
    def stations(self) -> tuple[Station, ...]: ...

    def search(self, request: NormalizedJourneyRequest) -> SearchMaterials: ...


@dataclass(frozen=True)
class RecoveryRequest:
    trip_id: str
    current_station_id: str
    expected_state_version: int
    recovery_cutoff_utc: datetime
    initial_decision_snapshot: dict[str, object]

    def __post_init__(self) -> None:
        require_utc(self.recovery_cutoff_utc, "recovery_cutoff_utc")


@dataclass(frozen=True)
class RecoveryMaterials:
    candidates: tuple[CandidateItinerary, ...]
    continuation_policy_key: str
    decision_context: DecisionContext
    trigger: RecoveryTriggerInput


class RecoveryBackend(Protocol):
    def recovery(self, request: RecoveryRequest) -> RecoveryMaterials: ...


@dataclass(frozen=True)
class ServiceConfig:
    allowed_hosts: frozenset[str]
    allowed_origins: frozenset[str]
    decision_keys: tuple[tuple[str, bytes], ...]
    active_decision_key_version: str
    trip_keys: tuple[tuple[str, bytes], ...]
    active_trip_key_version: str
    loopback_only: bool = True
    trusted_proxy_addresses: frozenset[str] = frozenset()
    maximum_body_bytes: int = 32 * 1024
    decision_ttl_seconds: int = 10 * 60
    trip_ttl_seconds: int = 6 * 60 * 60
    maximum_sse_events: int = 100
    maximum_sse_bytes: int = 64 * 1024
    maximum_sse_age_seconds: int = 10 * 60
    search_limit_per_minute: int = 30
    trip_creation_limit_per_hour: int = 10
    state_limit_per_minute: int = 60

    def __post_init__(self) -> None:
        if not self.allowed_hosts or not self.allowed_origins:
            raise ValueError("exact Host and Origin allow-lists are required")
        self._validate_keyring(self.decision_keys, self.active_decision_key_version, "decision")
        self._validate_keyring(self.trip_keys, self.active_trip_key_version, "trip")
        bounds = (
            self.maximum_body_bytes,
            self.decision_ttl_seconds,
            self.trip_ttl_seconds,
            self.maximum_sse_events,
            self.maximum_sse_bytes,
            self.maximum_sse_age_seconds,
            self.search_limit_per_minute,
            self.trip_creation_limit_per_hour,
            self.state_limit_per_minute,
        )
        if any(value <= 0 for value in bounds):
            raise ValueError("service resource bounds must be positive")

    @staticmethod
    def _validate_keyring(keys: tuple[tuple[str, bytes], ...], active: str, label: str) -> None:
        keyring = dict(keys)
        if len(keyring) != len(keys) or active not in keyring:
            raise ValueError(f"{label} keyring is invalid")
        if any(len(secret) < 32 for secret in keyring.values()):
            raise ValueError(f"{label} HMAC keys must contain at least 256 bits")
