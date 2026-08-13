"""Immutable contracts shared by replay, live scoring, and the service API."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from arrive90_data_contracts.candidates import CandidateItinerary
from arrive90_data_contracts.realtime import require_utc


class DecisionStatus(StrEnum):
    TARGET_MET = "TARGET_MET"
    TARGET_NOT_MET = "TARGET_NOT_MET"
    DEGRADED_SCHEDULE_ONLY = "DEGRADED_SCHEDULE_ONLY"
    STALE_LIVE_DATA = "STALE_LIVE_DATA"
    MODEL_ABSTAINED = "MODEL_ABSTAINED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    NO_SUPPORTED_ITINERARY = "NO_SUPPORTED_ITINERARY"


class ScoringState(StrEnum):
    READY = "READY"
    STALE = "STALE"
    ABSTAINED = "ABSTAINED"


class RecoveryReason(StrEnum):
    CAUSAL_CLOSURE = "CAUSAL_CLOSURE"
    LOW_TRANSFER_PROBABILITY = "LOW_TRANSFER_PROBABILITY"


class RecoveryStatus(StrEnum):
    RECOVERY_ACTION_AVAILABLE = "RECOVERY_ACTION_AVAILABLE"
    NO_DISTINCT_RECOVERY_ACTION = "NO_DISTINCT_RECOVERY_ACTION"


class TripState(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    ON_FIRST_LEG = "ON_FIRST_LEG"
    AT_TRANSFER = "AT_TRANSFER"
    ON_FINAL_LEG = "ON_FINAL_LEG"
    ENDED = "ENDED"


@dataclass(frozen=True)
class QuantileEstimate:
    level: str
    arrival_utc: datetime
    support_cell_id: str

    def __post_init__(self) -> None:
        require_utc(self.arrival_utc, "arrival_utc")


@dataclass(frozen=True)
class CandidateScore:
    itinerary: CandidateItinerary
    calibrated_deadline_probability: float
    prediction_band_cell_id: str
    applicable_slice_cell_ids: tuple[str, ...]
    quantiles: tuple[QuantileEstimate, ...] = ()

    def __post_init__(self) -> None:
        if not 0.0 <= self.calibrated_deadline_probability <= 1.0:
            raise ValueError("calibrated deadline probability must be inside zero and one")
        if len(set(self.applicable_slice_cell_ids)) != len(self.applicable_slice_cell_ids):
            raise ValueError("applicable support cells must be unique")
        levels = tuple(item.level for item in self.quantiles)
        if len(set(levels)) != len(levels):
            raise ValueError("quantile levels must be unique")


@dataclass(frozen=True)
class EligibilityManifest:
    """One iteration-input support snapshot with fail-closed cell lookup."""

    known_cells: frozenset[str]
    eligible_cells: frozenset[str]
    target_cell_declarations: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def __post_init__(self) -> None:
        if not self.eligible_cells <= self.known_cells:
            raise ValueError("eligible cells must be present in known cells")
        targets = tuple(target for target, _cells in self.target_cell_declarations)
        if len(set(targets)) != len(targets):
            raise ValueError("target support declarations must be unique")

    def cell_is_eligible(self, cell_id: str) -> bool:
        return cell_id in self.known_cells and cell_id in self.eligible_cells

    def declared_target_is_supported(self, target: Decimal) -> bool:
        declarations = dict(self.target_cell_declarations)
        cells = declarations.get(format(target, ".2f"))
        if cells is None:
            return True
        return all(self.cell_is_eligible(cell_id) for cell_id in cells)


@dataclass(frozen=True)
class HorizonSupportManifest:
    supported_deadline_slack_regions: frozenset[str]

    def supports(self, region_id: str) -> bool:
        return region_id in self.supported_deadline_slack_regions


@dataclass(frozen=True)
class DecisionContext:
    decision_cutoff_utc: datetime
    context_id: str
    context_version: str
    candidate_manifest_hash: str
    candidate_eligibility: tuple[tuple[str, bool], ...]

    def __post_init__(self) -> None:
        require_utc(self.decision_cutoff_utc, "decision_cutoff_utc")
        keys = tuple(key for key, _eligible in self.candidate_eligibility)
        if len(set(keys)) != len(keys):
            raise ValueError("candidate eligibility keys must be unique")

    @property
    def mask(self) -> Mapping[str, bool]:
        return MappingProxyType(dict(self.candidate_eligibility))

    @property
    def eligibility_mask_hash(self) -> str:
        payload = json.dumps(
            self.candidate_eligibility,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode()
        return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class InitialDecisionRequest:
    ready_at_utc: datetime
    effective_deadline_at_utc: datetime
    reliability_target: Decimal
    maximum_extra_time_seconds: int
    deadline_slack_region_id: str

    def __post_init__(self) -> None:
        require_utc(self.ready_at_utc, "ready_at_utc")
        require_utc(self.effective_deadline_at_utc, "effective_deadline_at_utc")
        if self.reliability_target not in {Decimal("0.80"), Decimal("0.90"), Decimal("0.95")}:
            raise ValueError("unsupported reliability target")
        if not 0 <= self.maximum_extra_time_seconds <= 1_200:
            raise ValueError("maximum extra time must be from zero through 20 minutes")
        if self.effective_deadline_at_utc <= self.ready_at_utc:
            raise ValueError("effective deadline must follow ready time")


@dataclass(frozen=True)
class SelectedItinerary:
    policy_key: str
    planned_time_seconds: int
    extra_planned_time_seconds: int | None
    deadline_probability: Decimal | None
    diagnostic_probability: float | None
    quantile_arrivals: tuple[tuple[str, datetime], ...]
    model_output_status: str


@dataclass(frozen=True)
class InitialDecision:
    status: DecisionStatus
    comparator: SelectedItinerary | None
    cap_eligible_policy_keys: tuple[str, ...]
    recommendation: SelectedItinerary | None
    backup_itinerary: SelectedItinerary | None
    explanation_codes: tuple[str, ...]
    trip_start_supported: bool

    def canonical_payload(self) -> bytes:
        return _canonical_json(self)


@dataclass(frozen=True)
class RecoveryTriggerInput:
    state: TripState
    closure_applies: bool
    rounded_transfer_probability: Decimal | None
    candidate_decile_supported: bool
    station_supported: bool
    selected_policy_decile_supported: bool
    original_confirmatory_policy: bool
    recovery_ever_activated: bool


@dataclass(frozen=True)
class RecoveryDecision:
    reasons: tuple[RecoveryReason, ...]
    winning_reason: RecoveryReason
    status: RecoveryStatus
    continuation_comparator: SelectedItinerary
    cap_reference: SelectedItinerary
    selectable_policy_keys: tuple[str, ...]
    recommendation: SelectedItinerary | None
    backup_itinerary: SelectedItinerary | None

    def canonical_payload(self) -> bytes:
        return _canonical_json(self)


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, StrEnum):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return {name: _jsonable(getattr(value, name)) for name in value.__dataclass_fields__}
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    return value


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        _jsonable(value),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
