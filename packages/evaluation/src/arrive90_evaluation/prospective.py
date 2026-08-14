"""Immutable prospective-panel protocol, lineage ledger, and acceptance evaluation."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

from arrive90_routing.population import encode_key

from arrive90_evaluation.bootstrap import bootstrap_calibration_bound, bootstrap_policy_bounds
from arrive90_evaluation.metrics import (
    DEADLINE_BANDS,
    PolicyPairRow,
    PredictionRow,
    calibration_summary,
    deadline_band_id,
)

_SHA256 = re.compile(r"[0-9a-f]{64}")
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_SERVING_POLICY = "SERVING_HISTORICAL_V1"
_SHADOW_POLICY = "SHADOW_095_EVIDENCE_V1"


def _require_hash(value: str, label: str) -> None:
    if _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("prospective timestamps must be explicit UTC values")
    return parsed.astimezone(UTC)


def _json_value(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _json_value(member) for key, member in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_json_value(member) for member in value]
    if isinstance(value, (Decimal, StrEnum)):
        return str(value)
    return value


def canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            _json_value(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode()
    ).hexdigest()


class PanelPolicy(StrEnum):
    SERVING = _SERVING_POLICY
    SHADOW_095 = _SHADOW_POLICY


class AttemptStatus(StrEnum):
    DECIDED = "DECIDED"
    ABSTAINED = "ABSTAINED"
    COLLECTOR_FAILED = "COLLECTOR_FAILED"
    ROUTER_FAILED = "ROUTER_FAILED"
    MODEL_FAILED = "MODEL_FAILED"


class OutcomeStatus(StrEnum):
    SUCCESS_IDENTIFIED = "SUCCESS_IDENTIFIED"
    FAILURE_IDENTIFIED = "FAILURE_IDENTIFIED"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True)
class ShakeoutDay:
    service_day: str
    scheduled_queries: int
    recorded_attempts: int
    collector_health_report_hash: str

    def __post_init__(self) -> None:
        date.fromisoformat(self.service_day)
        if self.scheduled_queries <= 0 or self.recorded_attempts < 0:
            raise ValueError("shakeout query counts are invalid")
        _require_hash(self.collector_health_report_hash, "collector health report")


def build_shakeout_report(
    days: tuple[ShakeoutDay, ...], *, open_operational_defects: int
) -> dict[str, Any]:
    if open_operational_defects < 0:
        raise ValueError("open defect count cannot be negative")
    distinct_days = {item.service_day for item in days}
    checks = {
        "at_least_28_distinct_service_days": len(distinct_days) >= 28,
        "every_scheduled_shakeout_query_recorded": all(
            item.recorded_attempts == item.scheduled_queries for item in days
        ),
        "operational_defects_closed": open_operational_defects == 0,
    }
    return {
        "checks": checks,
        "failing_checks": sorted(key for key, value in checks.items() if not value),
        "health_report_hashes": sorted(
            {item.collector_health_report_hash for item in days}, key=str.encode
        ),
        "open_operational_defects": open_operational_defects,
        "recorded_attempts": sum(item.recorded_attempts for item in days),
        "scheduled_queries": sum(item.scheduled_queries for item in days),
        "service_day_blocks": len(distinct_days),
        "status": "PASSED" if all(checks.values()) else "FAILED",
    }


@dataclass(frozen=True)
class BandPlan:
    band_id: str
    planned_decisions: int
    planned_base_queries: int
    planned_service_days: int
    calibration_upper_95_max: float = 0.05

    def __post_init__(self) -> None:
        eligible_bands = {
            f"[{lower:.2f},{upper:.2f}{']' if right_closed else ')'}"
            for lower, upper, right_closed in DEADLINE_BANDS
            if upper <= Decimal("0.95")
        }
        if self.band_id not in eligible_bands:
            raise ValueError("serving band plans must name a frozen band below 0.95")
        if (
            self.planned_decisions < 500
            or self.planned_base_queries < 250
            or self.planned_service_days < 50
        ):
            raise ValueError("serving band plans must meet the Section 6.1 support minimums")
        if self.calibration_upper_95_max != 0.05:
            raise ValueError("serving calibration tolerance is frozen at 0.05")


@dataclass(frozen=True)
class PrecisionPlan:
    historical_block_variance: float
    shakeout_block_variance: float
    assumed_intraday_correlation: float
    independent_service_day_blocks: int
    attrition_fraction: float
    distinct_base_queries: int
    planned_serving_decisions: int
    planned_shadow_decisions: int
    planned_shadow_base_queries: int
    raw_decisions: int
    weighted_mass: float
    cluster_adjusted_effective_sample_size: float
    desired_half_width: float = 0.03

    def __post_init__(self) -> None:
        if min(self.historical_block_variance, self.shakeout_block_variance) < 0:
            raise ValueError("precision variances cannot be negative")
        if not 0 <= self.assumed_intraday_correlation <= 1:
            raise ValueError("intraday correlation must be between zero and one")
        if not 0 <= self.attrition_fraction < 1:
            raise ValueError("attrition must be at least zero and less than one")
        if self.independent_service_day_blocks < 56:
            raise ValueError("the final panel requires at least 56 service-day blocks")
        if self.distinct_base_queries < 1_000 or self.planned_serving_decisions < 2_000:
            raise ValueError("the serving panel misses its frozen planned count minimum")
        if self.planned_shadow_decisions < 800 or self.planned_shadow_base_queries < 400:
            raise ValueError("the 0.95 shadow panel misses its frozen planned count minimum")
        if (
            min(
                self.raw_decisions,
                self.weighted_mass,
                self.cluster_adjusted_effective_sample_size,
            )
            <= 0
        ):
            raise ValueError("precision population measures must be positive")
        if self.raw_decisions * (1 - self.attrition_fraction) < (
            self.planned_serving_decisions + self.planned_shadow_decisions
        ):
            raise ValueError("raw panel size does not preserve frozen counts after attrition")
        if self.cluster_adjusted_effective_sample_size > self.raw_decisions:
            raise ValueError("cluster-adjusted effective sample size cannot exceed raw decisions")
        if self.desired_half_width != 0.03:
            raise ValueError("the prospective confidence-interval target is frozen at 0.03")
        if self.planned_half_width > self.desired_half_width:
            raise ValueError("the conservative precision calculation misses its target")

    @property
    def selected_block_variance(self) -> float:
        return max(self.historical_block_variance, self.shakeout_block_variance)

    @property
    def planned_half_width(self) -> float:
        return 1.96 * math.sqrt(self.selected_block_variance / self.independent_service_day_blocks)


@dataclass(frozen=True)
class PanelScenario:
    query_id: str
    base_query_id: str
    service_day: str
    scheduled_at_utc: str
    outcome_not_before_utc: str
    origin_station_id: str
    destination_station_id: str
    deadline_slack_minutes: int
    reliability_target: Decimal
    maximum_extra_minutes: int
    policy: PanelPolicy

    def __post_init__(self) -> None:
        if (
            _SAFE_ID.fullmatch(self.query_id) is None
            or _SAFE_ID.fullmatch(self.base_query_id) is None
        ):
            raise ValueError("prospective query identifiers are invalid")
        scheduled = _parse_utc(self.scheduled_at_utc)
        outcome_not_before = _parse_utc(self.outcome_not_before_utc)
        if outcome_not_before <= scheduled:
            raise ValueError("outcome resolution must open after the scheduled query")
        date.fromisoformat(self.service_day)
        if self.origin_station_id == self.destination_station_id:
            raise ValueError("prospective queries require distinct stations")
        if not 5 <= self.deadline_slack_minutes <= 180 or self.deadline_slack_minutes % 5:
            raise ValueError("deadline slack must use the frozen five-minute grid")
        if self.reliability_target not in {Decimal("0.80"), Decimal("0.90"), Decimal("0.95")}:
            raise ValueError("reliability target is outside the public lattice")
        if not 0 <= self.maximum_extra_minutes <= 20:
            raise ValueError("maximum extra time is outside the public lattice")
        if self.policy is PanelPolicy.SHADOW_095 and (
            self.reliability_target != Decimal("0.95") or self.maximum_extra_minutes != 20
        ):
            raise ValueError("the nonserving shadow policy is fixed at target 0.95 and cap 20")


@dataclass(frozen=True)
class FrozenPanel:
    acceptance_version: str
    panel_id: str
    frozen_at_utc: str
    fixed_end_date: str
    shakeout_service_days: int
    shakeout_report_hash: str
    acceptance_charter_hash: str
    historical_bundle_hash: str
    candidate_configuration_hash: str
    freshness_rules_hash: str
    support_policy_hash: str
    decision_policy_hash: str
    outcome_resolver_hash: str
    online_offline_parity_hash: str
    precision_plan: PrecisionPlan
    serving_band_plans: tuple[BandPlan, ...]
    scenarios: tuple[PanelScenario, ...]
    model_schema: str = "historical_v1"
    candidate_generator_mode: str = "STATIC_ROUTE_POLICY_V1"
    shadow_policy: str = _SHADOW_POLICY
    outcomes_opened_before_freeze: bool = False

    def __post_init__(self) -> None:
        if self.acceptance_version != "v1" or _SAFE_ID.fullmatch(self.panel_id) is None:
            raise ValueError("prospective panel identity is invalid")
        frozen_at = _parse_utc(self.frozen_at_utc)
        fixed_end = date.fromisoformat(self.fixed_end_date)
        if self.shakeout_service_days < 28:
            raise ValueError("the operational shakeout requires 28 service days")
        for label, value in (
            ("shakeout report", self.shakeout_report_hash),
            ("acceptance charter", self.acceptance_charter_hash),
            ("historical bundle", self.historical_bundle_hash),
            ("candidate configuration", self.candidate_configuration_hash),
            ("freshness rules", self.freshness_rules_hash),
            ("support policy", self.support_policy_hash),
            ("decision policy", self.decision_policy_hash),
            ("outcome resolver", self.outcome_resolver_hash),
            ("online-offline parity", self.online_offline_parity_hash),
        ):
            _require_hash(value, label)
        if self.model_schema != "historical_v1":
            raise ValueError(
                "the first panel evaluates historical_v1 and cannot train prospective_v2"
            )
        if self.candidate_generator_mode != "STATIC_ROUTE_POLICY_V1":
            raise ValueError("the first prospective panel must use static route policies")
        if self.shadow_policy != _SHADOW_POLICY:
            raise ValueError("the nonserving 0.95 shadow policy identifier is immutable")
        if self.outcomes_opened_before_freeze:
            raise ValueError("a prospective panel cannot freeze after outcome access")
        if not self.scenarios or not self.serving_band_plans:
            raise ValueError("a frozen panel requires scenarios and serving band plans")
        if len(self.scenarios) != self.precision_plan.raw_decisions:
            raise ValueError("the frozen raw-decision count must equal the scheduled panel")
        query_ids = {item.query_id for item in self.scenarios}
        if len(query_ids) != len(self.scenarios):
            raise ValueError("scheduled prospective query identifiers must be unique")
        if any(_parse_utc(item.scheduled_at_utc) <= frozen_at for item in self.scenarios):
            raise ValueError("every panel query must be scheduled after the freeze timestamp")
        service_days = {date.fromisoformat(item.service_day) for item in self.scenarios}
        if len(service_days) < 56 or fixed_end < min(service_days) + timedelta(days=55):
            raise ValueError("the fixed panel must span at least 56 service-day blocks")
        if max(service_days) > fixed_end:
            raise ValueError("no scheduled query may extend beyond the fixed panel end")
        serving = tuple(item for item in self.scenarios if item.policy is PanelPolicy.SERVING)
        shadow = tuple(item for item in self.scenarios if item.policy is PanelPolicy.SHADOW_095)
        if len(serving) < self.precision_plan.planned_serving_decisions:
            raise ValueError("manifest omits planned serving-policy decisions")
        if (
            len({item.base_query_id for item in serving})
            < self.precision_plan.distinct_base_queries
        ):
            raise ValueError("manifest omits planned serving base queries")
        if len(shadow) < self.precision_plan.planned_shadow_decisions:
            raise ValueError("manifest omits planned 0.95 shadow decisions")
        if (
            len({item.base_query_id for item in shadow})
            < self.precision_plan.planned_shadow_base_queries
        ):
            raise ValueError("manifest omits planned 0.95 shadow base queries")
        if len({item.service_day for item in shadow}) < 56:
            raise ValueError("the 0.95 shadow plan must span 56 service-day blocks")

    @property
    def manifest_hash(self) -> str:
        return canonical_hash(asdict(self))

    @property
    def latest_outcome_not_before(self) -> datetime:
        return max(_parse_utc(item.outcome_not_before_utc) for item in self.scenarios)


def balanced_lattice_assignments(
    keys: tuple[tuple[str, ...], ...], *, public_seed: str
) -> dict[tuple[str, ...], tuple[Decimal, int]]:
    """Apply the frozen HMAC ordering and ordered 63-member public lattice."""

    if not keys or len(set(keys)) != len(keys):
        raise ValueError("prospective lattice keys must be nonempty and unique")
    ordered = sorted(
        keys,
        key=lambda key: (
            hmac.digest(public_seed.encode(), encode_key(key), "sha256"),
            encode_key(key),
        ),
    )
    lattice = tuple(
        (target, cap)
        for target in (Decimal("0.80"), Decimal("0.90"), Decimal("0.95"))
        for cap in range(21)
    )
    return {key: lattice[index % 63] for index, key in enumerate(ordered)}


def freeze_panel(*, panel: FrozenPanel, shakeout_report: dict[str, Any]) -> FrozenPanel:
    """Authorize a panel freeze only from a complete, defect-free shakeout report."""

    if shakeout_report.get("status") != "PASSED":
        raise ValueError("the 28-service-day shakeout has not passed")
    if shakeout_report.get("service_day_blocks", 0) < 28:
        raise ValueError("the shakeout report contains fewer than 28 service days")
    if shakeout_report.get("open_operational_defects") != 0:
        raise ValueError("operational defects must be closed before panel freeze")
    if canonical_hash(shakeout_report) != panel.shakeout_report_hash:
        raise ValueError("the panel does not bind the supplied shakeout report")
    return panel


@dataclass(frozen=True)
class DecisionResult:
    decision_id: str
    rounded_probability: Decimal
    unrounded_probability: float
    model_schema: str
    served_to_rider: bool

    def __post_init__(self) -> None:
        if _SAFE_ID.fullmatch(self.decision_id) is None:
            raise ValueError("prospective decision identifier is invalid")
        if not Decimal("0") <= self.rounded_probability <= Decimal("1"):
            raise ValueError("rounded decision probability is outside zero and one")
        if not 0 <= self.unrounded_probability <= 1:
            raise ValueError("decision probability is outside zero and one")
        if self.model_schema != "historical_v1":
            raise ValueError("the frozen panel cannot train or score prospective_v2")
        if self.served_to_rider:
            raise ValueError("prospective recommendations cannot be sent to real riders")

    @property
    def decision_hash(self) -> str:
        return canonical_hash(asdict(self))


@dataclass(frozen=True)
class DecisionLineage:
    feed_blob_hashes: tuple[str, ...]
    fetch_attempt_hashes: tuple[str, ...]
    candidate_manifest_hash: str
    feature_row_hash: str
    model_bundle_hash: str
    decision_context_hash: str
    decision_hash: str

    def __post_init__(self) -> None:
        if not self.feed_blob_hashes or not self.fetch_attempt_hashes:
            raise ValueError("decision lineage requires feed blobs and fetch attempts")
        for label, value in (
            *(("feed blob", item) for item in self.feed_blob_hashes),
            *(("fetch attempt", item) for item in self.fetch_attempt_hashes),
            ("candidate manifest", self.candidate_manifest_hash),
            ("feature row", self.feature_row_hash),
            ("model bundle", self.model_bundle_hash),
            ("decision context", self.decision_context_hash),
            ("decision", self.decision_hash),
        ):
            _require_hash(value, label)


@dataclass(frozen=True)
class QueryAttempt:
    attempt_id: str
    query_id: str
    attempted_at_utc: str
    status: AttemptStatus
    latency_ms: float
    feed_header_age_seconds: int | None
    route_entity_age_seconds: int | None
    candidate_feature_age_seconds: int | None
    failure_code: str | None = None
    decision: DecisionResult | None = None
    lineage: DecisionLineage | None = None

    def __post_init__(self) -> None:
        if _SAFE_ID.fullmatch(self.attempt_id) is None or _SAFE_ID.fullmatch(self.query_id) is None:
            raise ValueError("prospective attempt identity is invalid")
        _parse_utc(self.attempted_at_utc)
        if self.latency_ms < 0:
            raise ValueError("attempt latency cannot be negative")
        if self.status is AttemptStatus.DECIDED:
            if self.decision is None or self.lineage is None:
                raise ValueError("a decided query requires a decision and full lineage")
            if self.lineage.decision_hash != self.decision.decision_hash:
                raise ValueError("decision content does not match its lineage hash")
        elif self.decision is not None or self.lineage is not None or self.failure_code is None:
            raise ValueError("a non-decision requires only an explicit failure code")


@dataclass(frozen=True)
class OutcomeResolution:
    decision_id: str
    resolved_at_utc: str
    status: OutcomeStatus
    selected_success: bool | None
    comparator_success: bool | None
    censoring_reason: str | None = None

    def __post_init__(self) -> None:
        if _SAFE_ID.fullmatch(self.decision_id) is None:
            raise ValueError("prospective outcome identity is invalid")
        _parse_utc(self.resolved_at_utc)
        if self.status is OutcomeStatus.SUCCESS_IDENTIFIED and self.selected_success is not True:
            raise ValueError("a success resolution requires selected success")
        if self.status is OutcomeStatus.FAILURE_IDENTIFIED and self.selected_success is not False:
            raise ValueError("a failure resolution requires selected failure")
        if self.status is OutcomeStatus.UNRESOLVED and (
            self.selected_success is not None or self.censoring_reason is None
        ):
            raise ValueError("an unresolved outcome requires censoring provenance")


@dataclass(frozen=True)
class LineageInventory:
    feed_blob_hashes: frozenset[str]
    fetch_attempt_hashes: frozenset[str]
    candidate_manifest_hashes: frozenset[str]
    feature_row_hashes: frozenset[str]
    decision_context_hashes: frozenset[str]

    def __post_init__(self) -> None:
        groups = (
            self.feed_blob_hashes,
            self.fetch_attempt_hashes,
            self.candidate_manifest_hashes,
            self.feature_row_hashes,
            self.decision_context_hashes,
        )
        if any(not group for group in groups):
            raise ValueError("lineage inventory groups must be nonempty")
        for group in groups:
            for value in group:
                _require_hash(value, "lineage inventory member")


class ImmutablePanelStore:
    """Create-only storage for a frozen manifest, query attempts, and matured outcomes."""

    def __init__(self, root: Path) -> None:
        self.root = root

    @staticmethod
    def _write(path: Path, value: object) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as stream:
            json.dump(_json_value(value), stream, indent=2, sort_keys=True)
            stream.write("\n")
        return path

    def write_manifest(self, panel: FrozenPanel) -> Path:
        return self._write(self.root / "manifest.json", asdict(panel))

    def load_manifest(self) -> FrozenPanel:
        payload = json.loads((self.root / "manifest.json").read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("prospective manifest must be a JSON object")
        return panel_from_dict(payload)

    def record_attempt(self, panel: FrozenPanel, attempt: QueryAttempt) -> Path:
        scenarios = {item.query_id: item for item in panel.scenarios}
        scenario = scenarios.get(attempt.query_id)
        if scenario is None:
            raise ValueError("attempt does not belong to the frozen panel")
        attempted = _parse_utc(attempt.attempted_at_utc)
        scheduled = _parse_utc(scenario.scheduled_at_utc)
        if attempted < scheduled:
            raise ValueError("query attempt cannot precede its frozen scheduled time")
        if (
            attempt.decision is not None
            and scenario.policy is PanelPolicy.SHADOW_095
            and attempt.decision.rounded_probability < Decimal("0.95")
        ):
            raise ValueError("a retained 0.95 shadow selection must be in the final band")
        return self._write(self.root / "attempts" / f"{attempt.query_id}.json", asdict(attempt))

    def record_outcome(
        self,
        panel: FrozenPanel,
        attempts: tuple[QueryAttempt, ...],
        outcome: OutcomeResolution,
    ) -> Path:
        by_decision = {
            attempt.decision.decision_id: attempt
            for attempt in attempts
            if attempt.decision is not None
        }
        attempt = by_decision.get(outcome.decision_id)
        if attempt is None:
            raise ValueError("outcome does not reference a retained panel decision")
        scenario = next(item for item in panel.scenarios if item.query_id == attempt.query_id)
        if _parse_utc(outcome.resolved_at_utc) < _parse_utc(scenario.outcome_not_before_utc):
            raise ValueError("outcomes cannot be resolved before the frozen journey horizon")
        return self._write(self.root / "outcomes" / f"{outcome.decision_id}.json", asdict(outcome))

    def load_attempts(self) -> tuple[QueryAttempt, ...]:
        return tuple(
            attempt_from_dict(json.loads(path.read_text(encoding="utf-8")))
            for path in sorted((self.root / "attempts").glob("*.json"), key=lambda item: item.name)
        )

    def load_outcomes(self) -> tuple[OutcomeResolution, ...]:
        return tuple(
            outcome_from_dict(json.loads(path.read_text(encoding="utf-8")))
            for path in sorted((self.root / "outcomes").glob("*.json"), key=lambda item: item.name)
        )


def panel_from_dict(value: dict[str, Any]) -> FrozenPanel:
    payload = dict(value)
    precision_value = payload.pop("precision_plan")
    band_values = payload.pop("serving_band_plans")
    scenario_values = payload.pop("scenarios")
    if (
        not isinstance(precision_value, dict)
        or not isinstance(band_values, (list, tuple))
        or not isinstance(scenario_values, (list, tuple))
    ):
        raise ValueError("prospective manifest has invalid nested values")
    scenarios = tuple(
        PanelScenario(
            **{
                **scenario,
                "reliability_target": Decimal(scenario["reliability_target"]),
                "policy": PanelPolicy(scenario["policy"]),
            }
        )
        for scenario in scenario_values
    )
    return FrozenPanel(
        **payload,
        precision_plan=PrecisionPlan(**precision_value),
        serving_band_plans=tuple(BandPlan(**item) for item in band_values),
        scenarios=scenarios,
    )


def attempt_from_dict(value: dict[str, Any]) -> QueryAttempt:
    payload = dict(value)
    decision_value = payload.pop("decision", None)
    lineage_value = payload.pop("lineage", None)
    decision = None
    if decision_value is not None:
        decision = DecisionResult(
            **{
                **decision_value,
                "rounded_probability": Decimal(decision_value["rounded_probability"]),
            }
        )
    lineage = None
    if lineage_value is not None:
        lineage = DecisionLineage(
            **{
                **lineage_value,
                "feed_blob_hashes": tuple(lineage_value["feed_blob_hashes"]),
                "fetch_attempt_hashes": tuple(lineage_value["fetch_attempt_hashes"]),
            }
        )
    status = AttemptStatus(payload.pop("status"))
    return QueryAttempt(
        **payload,
        status=status,
        decision=decision,
        lineage=lineage,
    )


def outcome_from_dict(value: dict[str, Any]) -> OutcomeResolution:
    payload = dict(value)
    status = OutcomeStatus(payload.pop("status"))
    return OutcomeResolution(**payload, status=status)


def inventory_from_dict(value: dict[str, Any]) -> LineageInventory:
    return LineageInventory(
        feed_blob_hashes=frozenset(value.get("feed_blob_hashes", ())),
        fetch_attempt_hashes=frozenset(value.get("fetch_attempt_hashes", ())),
        candidate_manifest_hashes=frozenset(value.get("candidate_manifest_hashes", ())),
        feature_row_hashes=frozenset(value.get("feature_row_hashes", ())),
        decision_context_hashes=frozenset(value.get("decision_context_hashes", ())),
    )


def _lineage_is_complete(
    attempt: QueryAttempt, panel: FrozenPanel, inventory: LineageInventory
) -> bool:
    if attempt.decision is None or attempt.lineage is None:
        return False
    lineage = attempt.lineage
    return (
        lineage.model_bundle_hash == panel.historical_bundle_hash
        and lineage.decision_hash == attempt.decision.decision_hash
        and set(lineage.feed_blob_hashes) <= inventory.feed_blob_hashes
        and set(lineage.fetch_attempt_hashes) <= inventory.fetch_attempt_hashes
        and lineage.candidate_manifest_hash in inventory.candidate_manifest_hashes
        and lineage.feature_row_hash in inventory.feature_row_hashes
        and lineage.decision_context_hash in inventory.decision_context_hashes
    )


def _percentile(values: tuple[float, ...], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[math.ceil((len(ordered) - 1) * fraction)]


def _prediction_rows(
    attempts: tuple[QueryAttempt, ...],
    outcomes: dict[str, OutcomeResolution],
    scenarios: dict[str, PanelScenario],
    policy: PanelPolicy,
) -> tuple[PredictionRow, ...]:
    rows: list[PredictionRow] = []
    for attempt in attempts:
        scenario = scenarios.get(attempt.query_id)
        if scenario is None or scenario.policy is not policy or attempt.decision is None:
            continue
        outcome = outcomes.get(attempt.decision.decision_id)
        rows.append(
            PredictionRow(
                attempt.decision.decision_id,
                scenario.base_query_id,
                scenario.service_day,
                1.0,
                attempt.decision.rounded_probability,
                attempt.decision.unrounded_probability,
                outcome.selected_success if outcome is not None else None,
            )
        )
    return tuple(rows)


def evaluate_panel(
    *,
    panel: FrozenPanel,
    attempts: tuple[QueryAttempt, ...],
    outcomes: tuple[OutcomeResolution, ...],
    inventory: LineageInventory,
    historical_reference: dict[str, Any],
    historical_reference_hash: str,
    as_of_utc: str,
    bootstrap_seed: int,
    bootstrap_replicates: int = 2_000,
) -> dict[str, Any]:
    """Evaluate a frozen panel without hiding scheduled failures or unresolved outcomes."""

    _require_hash(historical_reference_hash, "historical reference")
    as_of = _parse_utc(as_of_utc)
    scenarios = {item.query_id: item for item in panel.scenarios}
    attempt_counts = Counter(item.query_id for item in attempts)
    outcome_counts = Counter(item.decision_id for item in outcomes)
    outcome_by_decision = {item.decision_id: item for item in outcomes}
    decided = tuple(item for item in attempts if item.status is AttemptStatus.DECIDED)
    decision_ids = {item.decision.decision_id for item in decided if item.decision is not None}
    panel_matured = as_of >= panel.latest_outcome_not_before
    every_query_recorded_once = set(attempt_counts) == set(scenarios) and all(
        count == 1 for count in attempt_counts.values()
    )
    every_decision_has_outcome = decision_ids == set(outcome_counts) and all(
        count == 1 for count in outcome_counts.values()
    )
    outcomes_after_horizon = all(
        _parse_utc(outcome.resolved_at_utc)
        >= _parse_utc(scenarios[attempt.query_id].outcome_not_before_utc)
        for attempt in decided
        if attempt.decision is not None
        for outcome in (outcome_by_decision.get(attempt.decision.decision_id),)
        if outcome is not None
    )
    lineage_complete = all(_lineage_is_complete(item, panel, inventory) for item in decided)
    historical_v1_only = all(
        item.decision is None or item.decision.model_schema == "historical_v1" for item in attempts
    )
    never_served_to_riders = all(
        item.decision is None or not item.decision.served_to_rider for item in attempts
    )
    shadow_never_serving = all(
        scenarios[item.query_id].policy is not PanelPolicy.SHADOW_095
        or item.decision is None
        or (
            item.decision.rounded_probability >= Decimal("0.95")
            and not item.decision.served_to_rider
        )
        for item in attempts
        if item.query_id in scenarios
    )
    serving_rows = _prediction_rows(attempts, outcome_by_decision, scenarios, PanelPolicy.SERVING)
    shadow_rows = _prediction_rows(attempts, outcome_by_decision, scenarios, PanelPolicy.SHADOW_095)
    band_reports: dict[str, Any] = {}
    band_checks: dict[str, bool] = {}
    for index, plan in enumerate(panel.serving_band_plans):
        members = tuple(
            row for row in serving_rows if deadline_band_id(row.rounded_probability) == plan.band_id
        )
        if members:
            summary = calibration_summary(members)
            bootstrap = (
                bootstrap_calibration_bound(
                    members, replicates=bootstrap_replicates, seed=bootstrap_seed + index
                )
                if summary.service_day_blocks >= 2
                else None
            )
            passes = (
                summary.decision_count >= plan.planned_decisions
                and summary.distinct_base_queries >= plan.planned_base_queries
                and summary.service_day_blocks >= plan.planned_service_days
                and bootstrap is not None
                and bootstrap.upper_95 <= plan.calibration_upper_95_max
            )
            band_reports[plan.band_id] = {
                "bootstrap": asdict(bootstrap) if bootstrap is not None else None,
                "summary": asdict(summary),
            }
        else:
            passes = False
            band_reports[plan.band_id] = {"bootstrap": None, "summary": None}
        band_checks[f"serving_band_{plan.band_id}_passes"] = passes
    shadow_summary = calibration_summary(shadow_rows) if shadow_rows else None
    shadow_bootstrap = None
    if shadow_summary is not None and shadow_summary.service_day_blocks >= 2:
        shadow_bootstrap = bootstrap_calibration_bound(
            shadow_rows, replicates=bootstrap_replicates, seed=bootstrap_seed + 100
        )
    shadow_counts_pass = bool(
        shadow_summary
        and shadow_summary.decision_count >= 800
        and shadow_summary.distinct_base_queries >= 400
        and shadow_summary.service_day_blocks >= 56
    )
    shadow_calibration_pass = bool(shadow_bootstrap and shadow_bootstrap.upper_95 <= 0.03)
    serving_count_pass = (
        len(serving_rows) >= 2_000 and len({row.base_query_id for row in serving_rows}) >= 1_000
    )
    policy_rows = tuple(
        PolicyPairRow(
            row.decision_id,
            row.base_query_id,
            row.service_day,
            row.weight,
            outcome_by_decision[row.decision_id].selected_success,
            outcome_by_decision[row.decision_id].comparator_success,
            None,
        )
        for row in serving_rows
        if row.decision_id in outcome_by_decision
    )
    policy_bootstrap = (
        bootstrap_policy_bounds(
            policy_rows, replicates=bootstrap_replicates, seed=bootstrap_seed + 200
        )
        if len({row.service_day for row in policy_rows}) >= 2
        else None
    )
    actual_half_width = (
        max(
            (
                policy_bootstrap.difference_lower.upper_95
                - policy_bootstrap.difference_lower.lower_95
            )
            / 2,
            (
                policy_bootstrap.difference_upper.upper_95
                - policy_bootstrap.difference_upper.lower_95
            )
            / 2,
        )
        if policy_bootstrap is not None
        else None
    )
    actual_precision_pass = actual_half_width is not None and actual_half_width <= 0.03
    integrity_checks = {
        "every_scheduled_query_recorded_exactly_once": every_query_recorded_once,
        "every_retained_decision_has_one_matured_outcome": every_decision_has_outcome,
        "historical_v1_only_and_no_prospective_v2_training": historical_v1_only,
        "lineage_inventory_replays_every_retained_decision": lineage_complete,
        "outcomes_resolved_only_after_frozen_horizon": outcomes_after_horizon,
        "panel_recommendations_never_served_to_riders": never_served_to_riders,
        "shadow_095_is_nonserving_and_final_band_only": shadow_never_serving,
    }
    evidence_checks = {
        "fixed_panel_has_matured": panel_matured,
        "planned_precision_calculation_passes": (
            panel.precision_plan.planned_half_width <= panel.precision_plan.desired_half_width
        ),
        "serving_policy_count_and_base_query_minimums_pass": serving_count_pass,
        "shadow_095_count_and_service_day_minimums_pass": shadow_counts_pass,
        "shadow_095_calibration_upper_bound_passes": shadow_calibration_pass,
        "prospective_primary_precision_passes": actual_precision_pass,
        **band_checks,
    }
    integrity_failures = sorted(key for key, value in integrity_checks.items() if not value)
    evidence_failures = sorted(key for key, value in evidence_checks.items() if not value)
    if panel_matured and integrity_failures:
        status = "FAILED"
    elif integrity_failures or evidence_failures:
        status = "INSUFFICIENT_EVIDENCE"
    else:
        status = "PASSED"
    failures = Counter(
        item.failure_code or item.status.value
        for item in attempts
        if item.status is not AttemptStatus.DECIDED
    )
    freshness = Counter(
        "UNKNOWN"
        if item.candidate_feature_age_seconds is None
        else "FRESH"
        if item.candidate_feature_age_seconds <= 90
        else "STALE"
        if item.candidate_feature_age_seconds <= 300
        else "UNUSABLE"
        for item in attempts
    )
    censoring = Counter(
        item.censoring_reason or "RESOLVED"
        for item in outcomes
        if item.status is OutcomeStatus.UNRESOLVED
    )
    latencies = tuple(item.latency_ms for item in attempts)
    prospective_summary = {
        "availability": {
            "available_decisions": len(decided),
            "rate": len(decided) / len(panel.scenarios),
            "scheduled_queries": len(panel.scenarios),
        },
        "calibration": {
            "serving_bands": band_reports,
            "shadow_095": {
                "bootstrap": asdict(shadow_bootstrap) if shadow_bootstrap else None,
                "summary": asdict(shadow_summary) if shadow_summary else None,
            },
        },
        "censoring_bounds": dict(sorted(censoring.items())),
        "failures": dict(sorted(failures.items())),
        "feed_freshness": dict(sorted(freshness.items())),
        "latency_ms": {
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
            "p99": _percentile(latencies, 0.99),
        },
        "policy_precision": {
            "actual_half_width": actual_half_width,
            "bootstrap": asdict(policy_bootstrap) if policy_bootstrap else None,
            "desired_half_width": panel.precision_plan.desired_half_width,
        },
    }
    return {
        "as_of_utc": as_of_utc,
        "checks": {**integrity_checks, **evidence_checks},
        "comparison": {
            "historical_replay": historical_reference,
            "historical_replay_artifact_hash": historical_reference_hash,
            "metric_namespace_rule": "HISTORICAL_AND_PROSPECTIVE_RESULTS_ARE_NEVER_POOLED",
            "prospective_shadow": prospective_summary,
        },
        "evidence_kind": "PROSPECTIVE_FROZEN_SHADOW_PANEL",
        "failing_checks": sorted(integrity_failures + evidence_failures),
        "fixed_end_date": panel.fixed_end_date,
        "integrity_failures": integrity_failures,
        "manifest_hash": panel.manifest_hash,
        "negative_results": sorted(set(integrity_failures + evidence_failures)),
        "panel_id": panel.panel_id,
        "panel_state": "MATURED" if panel_matured else "COLLECTING",
        "policies": {
            "serving": _SERVING_POLICY,
            "shadow_095": {
                "current_acceptance_contribution": "NONE",
                "identifier": _SHADOW_POLICY,
                "provisional_cell_status": "SERVING_INELIGIBLE",
                "user_visible": False,
            },
        },
        "precision_plan": {
            **asdict(panel.precision_plan),
            "planned_half_width": panel.precision_plan.planned_half_width,
            "selected_block_variance": panel.precision_plan.selected_block_variance,
        },
        "prospective": prospective_summary,
        "status": status,
    }
