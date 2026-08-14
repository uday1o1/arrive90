"""Exercise the prospective protocol on explicit synthetic mechanics fixtures."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from arrive90_evaluation.prospective import (
    AttemptStatus,
    BandPlan,
    DecisionLineage,
    DecisionResult,
    FrozenPanel,
    LineageInventory,
    OutcomeResolution,
    OutcomeStatus,
    PanelPolicy,
    PanelScenario,
    PrecisionPlan,
    QueryAttempt,
    ShakeoutDay,
    balanced_lattice_assignments,
    build_shakeout_report,
    canonical_hash,
    evaluate_panel,
    freeze_panel,
)

ROOT = Path(__file__).resolve().parents[1]


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _fixture() -> tuple[
    FrozenPanel,
    tuple[QueryAttempt, ...],
    tuple[OutcomeResolution, ...],
    LineageInventory,
]:
    shakeout_days = tuple(
        ShakeoutDay(
            (date(2025, 11, 1) + timedelta(days=index)).isoformat(),
            40,
            40,
            _hash(f"synthetic-health-{index}"),
        )
        for index in range(28)
    )
    shakeout = build_shakeout_report(shakeout_days, open_operational_defects=0)
    start = datetime(2026, 1, 1, 12, tzinfo=UTC)
    keys = tuple((f"serving-{index}",) for index in range(2_200))
    assignments = balanced_lattice_assignments(keys, public_seed="arrive90-v1-public-query-seed")
    scenarios: list[PanelScenario] = []
    for index, key in enumerate(keys):
        scheduled = start + timedelta(days=index % 56, seconds=index // 56)
        target, cap = assignments[key]
        scenarios.append(
            PanelScenario(
                query_id=key[0],
                base_query_id=f"serving-base-{index % 1_100}",
                service_day=scheduled.date().isoformat(),
                scheduled_at_utc=_stamp(scheduled),
                outcome_not_before_utc=_stamp(scheduled + timedelta(minutes=210)),
                origin_station_id="synthetic-origin",
                destination_station_id="synthetic-destination",
                deadline_slack_minutes=5 + (index % 36) * 5,
                reliability_target=target,
                maximum_extra_minutes=cap,
                policy=PanelPolicy.SERVING,
            )
        )
    for index in range(896):
        scheduled = start + timedelta(days=index % 56, seconds=4_000 + index // 56)
        scenarios.append(
            PanelScenario(
                query_id=f"shadow-{index}",
                base_query_id=f"shadow-base-{index % 448}",
                service_day=scheduled.date().isoformat(),
                scheduled_at_utc=_stamp(scheduled),
                outcome_not_before_utc=_stamp(scheduled + timedelta(minutes=210)),
                origin_station_id="synthetic-origin",
                destination_station_id="synthetic-destination",
                deadline_slack_minutes=60,
                reliability_target=Decimal("0.95"),
                maximum_extra_minutes=20,
                policy=PanelPolicy.SHADOW_095,
            )
        )
    panel = FrozenPanel(
        acceptance_version="v1",
        panel_id="synthetic-protocol-qualification",
        frozen_at_utc="2025-12-31T00:00:00Z",
        fixed_end_date="2026-02-25",
        shakeout_service_days=28,
        shakeout_report_hash=canonical_hash(shakeout),
        acceptance_charter_hash=_file_hash(ROOT / "configs/acceptance/v1.yaml"),
        historical_bundle_hash=_hash("synthetic-historical-v1-bundle"),
        candidate_configuration_hash=_hash("synthetic-static-candidates"),
        freshness_rules_hash=_hash("synthetic-freshness-rules"),
        support_policy_hash=_hash("synthetic-support-policy"),
        decision_policy_hash=_hash("synthetic-decision-policy"),
        outcome_resolver_hash=_hash("synthetic-outcome-resolver"),
        online_offline_parity_hash=_hash("synthetic-parity-fixture"),
        precision_plan=PrecisionPlan(
            historical_block_variance=0.0049,
            shakeout_block_variance=0.0064,
            assumed_intraday_correlation=0.2,
            independent_service_day_blocks=56,
            attrition_fraction=0.05,
            distinct_base_queries=1_000,
            planned_serving_decisions=2_000,
            planned_shadow_decisions=800,
            planned_shadow_base_queries=400,
            raw_decisions=3_096,
            weighted_mass=3_096,
            cluster_adjusted_effective_sample_size=56,
        ),
        serving_band_plans=(BandPlan("[0.90,0.95)", 2_000, 1_000, 50),),
        scenarios=tuple(scenarios),
    )
    freeze_panel(panel=panel, shakeout_report=shakeout)
    hashes = {
        name: _hash(f"synthetic-{name}")
        for name in ("blob", "fetch", "candidate", "feature", "context")
    }
    attempts: list[QueryAttempt] = []
    outcomes: list[OutcomeResolution] = []
    for index, scenario in enumerate(panel.scenarios):
        is_shadow = scenario.policy is PanelPolicy.SHADOW_095
        probability = Decimal("0.970000") if is_shadow else Decimal("0.920000")
        decision = DecisionResult(
            decision_id=f"decision-{index}",
            rounded_probability=probability,
            unrounded_probability=float(probability),
            model_schema="historical_v1",
            served_to_rider=False,
        )
        attempts.append(
            QueryAttempt(
                attempt_id=f"attempt-{index}",
                query_id=scenario.query_id,
                attempted_at_utc=_stamp(
                    datetime.fromisoformat(scenario.scheduled_at_utc.replace("Z", "+00:00"))
                    + timedelta(seconds=1)
                ),
                status=AttemptStatus.DECIDED,
                latency_ms=25 + index % 10,
                feed_header_age_seconds=20,
                route_entity_age_seconds=30,
                candidate_feature_age_seconds=40,
                decision=decision,
                lineage=DecisionLineage(
                    feed_blob_hashes=(hashes["blob"],),
                    fetch_attempt_hashes=(hashes["fetch"],),
                    candidate_manifest_hash=hashes["candidate"],
                    feature_row_hash=hashes["feature"],
                    model_bundle_hash=panel.historical_bundle_hash,
                    decision_context_hash=hashes["context"],
                    decision_hash=decision.decision_hash,
                ),
            )
        )
        selected_success = index % 100 < (97 if is_shadow else 92)
        outcomes.append(
            OutcomeResolution(
                decision_id=decision.decision_id,
                resolved_at_utc=_stamp(
                    datetime.fromisoformat(scenario.outcome_not_before_utc.replace("Z", "+00:00"))
                    + timedelta(seconds=1)
                ),
                status=(
                    OutcomeStatus.SUCCESS_IDENTIFIED
                    if selected_success
                    else OutcomeStatus.FAILURE_IDENTIFIED
                ),
                selected_success=selected_success,
                comparator_success=index % 100 < 90,
            )
        )
    inventory = LineageInventory(
        feed_blob_hashes=frozenset({hashes["blob"]}),
        fetch_attempt_hashes=frozenset({hashes["fetch"]}),
        candidate_manifest_hashes=frozenset({hashes["candidate"]}),
        feature_row_hashes=frozenset({hashes["feature"]}),
        decision_context_hashes=frozenset({hashes["context"]}),
    )
    return panel, tuple(attempts), tuple(outcomes), inventory


def build_qualification() -> dict[str, Any]:
    panel, attempts, outcomes, inventory = _fixture()
    complete = evaluate_panel(
        panel=panel,
        attempts=attempts,
        outcomes=outcomes,
        inventory=inventory,
        historical_reference={"evidence_kind": "SYNTHETIC_HISTORICAL_CONTROL"},
        historical_reference_hash=_hash("synthetic-historical-control"),
        as_of_utc="2026-02-26T00:00:00Z",
        bootstrap_seed=8,
    )
    missing_attempt = attempts[0]
    if missing_attempt.decision is None:
        raise RuntimeError("synthetic decided fixture unexpectedly lacks a decision")
    omitted = evaluate_panel(
        panel=panel,
        attempts=attempts[1:],
        outcomes=tuple(
            item for item in outcomes if item.decision_id != missing_attempt.decision.decision_id
        ),
        inventory=inventory,
        historical_reference={"evidence_kind": "SYNTHETIC_HISTORICAL_CONTROL"},
        historical_reference_hash=_hash("synthetic-historical-control"),
        as_of_utc="2026-02-26T00:00:00Z",
        bootstrap_seed=9,
    )
    prospective = complete["prospective"]
    checks = {
        "complete_synthetic_protocol_control_passes": complete["status"] == "PASSED",
        "fixed_56_service_day_panel_is_enforced": len(
            {item.service_day for item in panel.scenarios}
        )
        == 56,
        "historical_and_prospective_namespaces_are_separate": complete["comparison"][
            "metric_namespace_rule"
        ]
        == "HISTORICAL_AND_PROSPECTIVE_RESULTS_ARE_NEVER_POOLED",
        "omitted_scheduled_query_seeded_defect_fails": omitted["status"] == "FAILED"
        and "every_scheduled_query_recorded_exactly_once" in omitted["integrity_failures"],
        "shadow_095_remains_nonserving": complete["policies"]["shadow_095"]["user_visible"] is False
        and complete["policies"]["shadow_095"]["current_acceptance_contribution"] == "NONE",
        "synthetic_calibration_and_precision_controls_pass": complete["checks"][
            "prospective_primary_precision_passes"
        ]
        and complete["checks"]["shadow_095_calibration_upper_bound_passes"],
    }
    return {
        "checks": checks,
        "evidence_kind": "SYNTHETIC_PROTOCOL_MECHANICS_ONLY",
        "failing_checks": sorted(key for key, value in checks.items() if not value),
        "input_hashes": {
            "acceptance_charter": _file_hash(ROOT / "configs/acceptance/v1.yaml"),
            "prospective_config": _file_hash(ROOT / "configs/evaluation/prospective-v1.yaml"),
            "prospective_implementation": _file_hash(
                ROOT / "packages/evaluation/src/arrive90_evaluation/prospective.py"
            ),
        },
        "measured_synthetic_control": {
            "availability": prospective["availability"],
            "panel_manifest_hash": panel.manifest_hash,
            "policy_precision": prospective["policy_precision"],
            "serving_band": prospective["calibration"]["serving_bands"]["[0.90,0.95)"],
            "shadow_095": prospective["calibration"]["shadow_095"],
        },
        "negative_control": {
            "failing_checks": omitted["failing_checks"],
            "seeded_defect": "OMIT_ONE_SCHEDULED_QUERY_AND_ITS_OUTCOME",
            "status": omitted["status"],
        },
        "status": "PASSED" if all(checks.values()) else "FAILED",
    }


def main() -> int:
    output = ROOT / "artifacts/reports/qualification/milestone-8-synthetic.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(build_qualification(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(output.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
