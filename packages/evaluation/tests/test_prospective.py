from __future__ import annotations

import dataclasses
import hashlib
import json
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from arrive90_evaluation.prospective import (
    AttemptStatus,
    BandPlan,
    DecisionLineage,
    DecisionResult,
    FrozenPanel,
    ImmutablePanelStore,
    LineageInventory,
    OutcomeResolution,
    OutcomeStatus,
    PanelPolicy,
    PanelScenario,
    PrecisionPlan,
    QueryAttempt,
    ShakeoutDay,
    attempt_from_dict,
    balanced_lattice_assignments,
    build_shakeout_report,
    canonical_hash,
    evaluate_panel,
    freeze_panel,
    inventory_from_dict,
    outcome_from_dict,
    panel_from_dict,
)

type PassingInputs = tuple[
    FrozenPanel,
    tuple[QueryAttempt, ...],
    tuple[OutcomeResolution, ...],
    LineageInventory,
]


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _stamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _shakeout() -> dict[str, object]:
    return {
        "status": "PASSED",
        "service_day_blocks": 28,
        "open_operational_defects": 0,
        "attempted_queries": 1_120,
    }


def _panel() -> FrozenPanel:
    start = datetime(2026, 1, 1, 12, tzinfo=UTC)
    serving: list[PanelScenario] = []
    assignments = balanced_lattice_assignments(
        tuple((f"serving-{index}",) for index in range(2_200)), public_seed="test-seed"
    )
    for index in range(2_200):
        scheduled = start + timedelta(days=index % 56, seconds=index // 56)
        target, cap = assignments[(f"serving-{index}",)]
        serving.append(
            PanelScenario(
                query_id=f"serving-{index}",
                base_query_id=f"serving-base-{index % 1_100}",
                service_day=scheduled.date().isoformat(),
                scheduled_at_utc=_stamp(scheduled),
                outcome_not_before_utc=_stamp(scheduled + timedelta(minutes=210)),
                origin_station_id="place-alfcl",
                destination_station_id="place-brmnl",
                deadline_slack_minutes=5 + (index % 36) * 5,
                reliability_target=target,
                maximum_extra_minutes=cap,
                policy=PanelPolicy.SERVING,
            )
        )
    shadow: list[PanelScenario] = []
    for index in range(896):
        scheduled = start + timedelta(days=index % 56, seconds=4_000 + index // 56)
        shadow.append(
            PanelScenario(
                query_id=f"shadow-{index}",
                base_query_id=f"shadow-base-{index % 448}",
                service_day=scheduled.date().isoformat(),
                scheduled_at_utc=_stamp(scheduled),
                outcome_not_before_utc=_stamp(scheduled + timedelta(minutes=210)),
                origin_station_id="place-brntn",
                destination_station_id="place-davis",
                deadline_slack_minutes=60,
                reliability_target=Decimal("0.95"),
                maximum_extra_minutes=20,
                policy=PanelPolicy.SHADOW_095,
            )
        )
    precision = PrecisionPlan(
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
    )
    shakeout = _shakeout()
    return FrozenPanel(
        acceptance_version="v1",
        panel_id="prospective-v1-test",
        frozen_at_utc="2025-12-31T00:00:00Z",
        fixed_end_date="2026-02-25",
        shakeout_service_days=28,
        shakeout_report_hash=canonical_hash(shakeout),
        acceptance_charter_hash=_hash("acceptance"),
        historical_bundle_hash=_hash("model"),
        candidate_configuration_hash=_hash("candidate"),
        freshness_rules_hash=_hash("freshness"),
        support_policy_hash=_hash("support"),
        decision_policy_hash=_hash("policy"),
        outcome_resolver_hash=_hash("outcomes"),
        online_offline_parity_hash=_hash("parity"),
        precision_plan=precision,
        serving_band_plans=(BandPlan("[0.90,0.95)", 2_000, 1_000, 50),),
        scenarios=tuple(serving + shadow),
    )


def _evidence(
    panel: FrozenPanel,
) -> tuple[tuple[QueryAttempt, ...], tuple[OutcomeResolution, ...], LineageInventory]:
    blob = _hash("blob")
    fetch = _hash("fetch")
    candidate = _hash("candidate-manifest")
    feature = _hash("feature")
    context = _hash("context")
    attempts: list[QueryAttempt] = []
    outcomes: list[OutcomeResolution] = []
    for index, scenario in enumerate(panel.scenarios):
        shadow = scenario.policy is PanelPolicy.SHADOW_095
        probability = Decimal("0.970000") if shadow else Decimal("0.920000")
        decision = DecisionResult(
            decision_id=f"decision-{index}",
            rounded_probability=probability,
            unrounded_probability=float(probability),
            model_schema="historical_v1",
            served_to_rider=False,
        )
        lineage = DecisionLineage(
            feed_blob_hashes=(blob,),
            fetch_attempt_hashes=(fetch,),
            candidate_manifest_hash=candidate,
            feature_row_hash=feature,
            model_bundle_hash=panel.historical_bundle_hash,
            decision_context_hash=context,
            decision_hash=decision.decision_hash,
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
                lineage=lineage,
            )
        )
        selected_success = index % 100 < (97 if shadow else 92)
        comparator_success = index % 100 < 90
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
                comparator_success=comparator_success,
            )
        )
    inventory = LineageInventory(
        feed_blob_hashes=frozenset({blob}),
        fetch_attempt_hashes=frozenset({fetch}),
        candidate_manifest_hashes=frozenset({candidate}),
        feature_row_hashes=frozenset({feature}),
        decision_context_hashes=frozenset({context}),
    )
    return tuple(attempts), tuple(outcomes), inventory


@pytest.fixture(scope="module")
def passing_inputs() -> PassingInputs:
    panel = _panel()
    attempts, outcomes, inventory = _evidence(panel)
    return panel, attempts, outcomes, inventory


@pytest.fixture(scope="module")
def passing_report(
    passing_inputs: PassingInputs,
) -> dict[str, object]:
    panel, attempts, outcomes, inventory = passing_inputs
    return evaluate_panel(
        panel=panel,
        attempts=attempts,
        outcomes=outcomes,
        inventory=inventory,
        historical_reference={"status": "PASSED", "availability": 0.99},
        historical_reference_hash=_hash("historical-report"),
        as_of_utc="2026-02-26T00:00:00Z",
        bootstrap_seed=8,
    )


def test_lattice_is_deterministic_and_balanced() -> None:
    keys = tuple((f"key-{index}", "deadline") for index in range(127))
    first = balanced_lattice_assignments(keys, public_seed="seed")
    second = balanced_lattice_assignments(tuple(reversed(keys)), public_seed="seed")
    assert first == second
    counts: dict[tuple[Decimal, int], int] = {}
    for member in first.values():
        counts[member] = counts.get(member, 0) + 1
    assert len(counts) == 63
    assert max(counts.values()) - min(counts.values()) == 1
    with pytest.raises(ValueError, match="nonempty and unique"):
        balanced_lattice_assignments((), public_seed="seed")


def test_freeze_requires_complete_shakeout() -> None:
    panel = _panel()
    assert freeze_panel(panel=panel, shakeout_report=_shakeout()) is panel
    for report in (
        {**_shakeout(), "status": "FAILED"},
        {**_shakeout(), "service_day_blocks": 27},
        {**_shakeout(), "open_operational_defects": 1},
        {**_shakeout(), "attempted_queries": 1_121},
    ):
        with pytest.raises(ValueError):
            freeze_panel(panel=panel, shakeout_report=report)


def test_shakeout_report_counts_every_scheduled_query() -> None:
    days = tuple(
        ShakeoutDay(
            service_day=(date(2025, 11, 1) + timedelta(days=index)).isoformat(),
            scheduled_queries=40,
            recorded_attempts=40,
            collector_health_report_hash=_hash(f"health-{index}"),
        )
        for index in range(28)
    )
    passing = build_shakeout_report(days, open_operational_defects=0)
    assert passing["status"] == "PASSED"
    missing = dataclasses.replace(days[-1], recorded_attempts=39)
    failed = build_shakeout_report((*days[:-1], missing), open_operational_defects=1)
    assert failed["status"] == "FAILED"
    assert failed["scheduled_queries"] == 1_120
    assert failed["recorded_attempts"] == 1_119
    with pytest.raises(ValueError, match="negative"):
        build_shakeout_report(days, open_operational_defects=-1)


def test_protocol_models_reject_unsafe_or_obsolete_values() -> None:
    panel = _panel()
    with pytest.raises(ValueError, match="prospective_v2"):
        dataclasses.replace(panel, model_schema="prospective_v2")
    with pytest.raises(ValueError, match="static route"):
        dataclasses.replace(panel, candidate_generator_mode="REALTIME_ROUTED_V2")
    with pytest.raises(ValueError, match="outcome access"):
        dataclasses.replace(panel, outcomes_opened_before_freeze=True)
    with pytest.raises(ValueError, match="confidence-interval target"):
        dataclasses.replace(panel.precision_plan, desired_half_width=0.04)
    with pytest.raises(ValueError, match="after attrition"):
        dataclasses.replace(panel.precision_plan, attrition_fraction=0.1)
    with pytest.raises(ValueError, match="cannot exceed raw"):
        dataclasses.replace(
            panel.precision_plan,
            cluster_adjusted_effective_sample_size=panel.precision_plan.raw_decisions + 1,
        )
    with pytest.raises(ValueError, match="equal the scheduled panel"):
        dataclasses.replace(
            panel,
            precision_plan=dataclasses.replace(
                panel.precision_plan, raw_decisions=panel.precision_plan.raw_decisions + 1
            ),
        )
    with pytest.raises(ValueError, match="support minimums"):
        BandPlan("[0.90,0.95)", 499, 250, 50)
    with pytest.raises(ValueError, match=r"below 0\.95"):
        BandPlan("[0.95,1.00]", 800, 400, 56)
    with pytest.raises(ValueError, match="fixed at target"):
        dataclasses.replace(panel.scenarios[-1], reliability_target=Decimal("0.90"))
    cross_midnight = dataclasses.replace(panel.scenarios[0], service_day="2025-12-31")
    assert cross_midnight.service_day == "2025-12-31"


def test_decision_and_outcome_contracts_fail_closed(passing_inputs: PassingInputs) -> None:
    panel, attempts, outcomes, _inventory = passing_inputs
    decision = attempts[0].decision
    assert decision is not None
    with pytest.raises(ValueError, match="real riders"):
        dataclasses.replace(decision, served_to_rider=True)
    with pytest.raises(ValueError, match="prospective_v2"):
        dataclasses.replace(decision, model_schema="prospective_v2")
    with pytest.raises(ValueError, match="full lineage"):
        dataclasses.replace(attempts[0], lineage=None)
    with pytest.raises(ValueError, match="explicit failure"):
        QueryAttempt(
            "failed",
            panel.scenarios[0].query_id,
            panel.scenarios[0].scheduled_at_utc,
            AttemptStatus.ROUTER_FAILED,
            1,
            None,
            None,
            None,
        )
    with pytest.raises(ValueError, match="selected success"):
        dataclasses.replace(outcomes[0], selected_success=False)
    with pytest.raises(ValueError, match="censoring provenance"):
        OutcomeResolution(
            "unresolved",
            "2026-03-01T00:00:00Z",
            OutcomeStatus.UNRESOLVED,
            None,
            None,
        )


def test_immutable_store_round_trips_and_rejects_rewrites(
    tmp_path: Path, passing_inputs: PassingInputs
) -> None:
    panel, attempts, outcomes, inventory = passing_inputs
    store = ImmutablePanelStore(tmp_path)
    store.write_manifest(panel)
    with pytest.raises(FileExistsError):
        store.write_manifest(panel)
    assert store.load_manifest().manifest_hash == panel.manifest_hash
    attempt = attempts[0]
    store.record_attempt(panel, attempt)
    with pytest.raises(FileExistsError):
        store.record_attempt(panel, attempt)
    assert store.load_attempts() == (attempt,)
    outcome = outcomes[0]
    with pytest.raises(ValueError, match="frozen journey horizon"):
        store.record_outcome(
            panel,
            (attempt,),
            dataclasses.replace(outcome, resolved_at_utc=panel.scenarios[0].scheduled_at_utc),
        )
    store.record_outcome(panel, (attempt,), outcome)
    assert store.load_outcomes() == (outcome,)
    with pytest.raises(FileExistsError):
        store.record_outcome(panel, (attempt,), outcome)
    inventory_payload = {
        field.name: sorted(getattr(inventory, field.name))
        for field in dataclasses.fields(inventory)
    }
    assert inventory_from_dict(inventory_payload) == inventory
    assert panel_from_dict(dataclasses.asdict(panel)) == panel
    assert attempt_from_dict(dataclasses.asdict(attempt)) == attempt
    assert outcome_from_dict(dataclasses.asdict(outcome)) == outcome


def test_complete_synthetic_panel_passes_actual_protocol_mechanics(
    passing_report: dict[str, object],
) -> None:
    assert passing_report["status"] == "PASSED"
    assert passing_report["panel_state"] == "MATURED"
    checks = passing_report["checks"]
    assert isinstance(checks, dict) and all(checks.values())
    comparison = passing_report["comparison"]
    assert isinstance(comparison, dict)
    assert comparison["metric_namespace_rule"] == (
        "HISTORICAL_AND_PROSPECTIVE_RESULTS_ARE_NEVER_POOLED"
    )
    policies = passing_report["policies"]
    assert isinstance(policies, dict)
    shadow = policies["shadow_095"]
    assert isinstance(shadow, dict)
    assert shadow["user_visible"] is False
    assert shadow["provisional_cell_status"] == "SERVING_INELIGIBLE"


def test_seeded_missing_query_fails_for_intended_reason(passing_inputs: PassingInputs) -> None:
    panel, attempts, outcomes, inventory = passing_inputs
    missing = attempts[0]
    assert missing.decision is not None
    report = evaluate_panel(
        panel=panel,
        attempts=attempts[1:],
        outcomes=tuple(
            item for item in outcomes if item.decision_id != missing.decision.decision_id
        ),
        inventory=inventory,
        historical_reference={},
        historical_reference_hash=_hash("history"),
        as_of_utc="2026-02-26T00:00:00Z",
        bootstrap_seed=9,
    )
    assert report["status"] == "FAILED"
    assert "every_scheduled_query_recorded_exactly_once" in report["integrity_failures"]
    assert report["prospective"]["availability"]["scheduled_queries"] == len(panel.scenarios)


def test_seeded_missing_lineage_fails_while_failure_attempts_remain_counted(
    passing_inputs: PassingInputs,
) -> None:
    panel, attempts, outcomes, inventory = passing_inputs
    bad_inventory = dataclasses.replace(
        inventory, feature_row_hashes=frozenset({_hash("not-the-feature")})
    )
    report = evaluate_panel(
        panel=panel,
        attempts=attempts,
        outcomes=outcomes,
        inventory=bad_inventory,
        historical_reference={},
        historical_reference_hash=_hash("history"),
        as_of_utc="2026-02-26T00:00:00Z",
        bootstrap_seed=10,
    )
    assert report["status"] == "FAILED"
    assert "lineage_inventory_replays_every_retained_decision" in report["integrity_failures"]


def test_unmatured_panel_is_insufficient_not_failed(passing_inputs: PassingInputs) -> None:
    panel, attempts, _outcomes, inventory = passing_inputs
    report = evaluate_panel(
        panel=panel,
        attempts=attempts[:10],
        outcomes=(),
        inventory=inventory,
        historical_reference={},
        historical_reference_hash=_hash("history"),
        as_of_utc="2026-01-02T00:00:00Z",
        bootstrap_seed=11,
    )
    assert report["status"] == "INSUFFICIENT_EVIDENCE"
    assert report["panel_state"] == "COLLECTING"


def test_public_cli_freeze_record_and_report_path(
    tmp_path: Path, passing_inputs: PassingInputs
) -> None:
    panel, attempts, outcomes, inventory = passing_inputs
    root = Path(__file__).resolve().parents[3]

    def run(*arguments: str) -> None:
        result = subprocess.run(  # noqa: S603 - exact repository CLI under test
            [sys.executable, str(root / "scripts/prospective_panel.py"), *arguments],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr

    def write(name: str, value: object) -> Path:
        path = tmp_path / name
        path.write_text(json.dumps(value, default=str, sort_keys=True) + "\n", encoding="utf-8")
        return path

    panel_path = write("panel.json", dataclasses.asdict(panel))
    shakeout_path = write("shakeout.json", _shakeout())
    store = tmp_path / "panel-store"
    run(
        "freeze",
        "--panel",
        str(panel_path),
        "--shakeout-report",
        str(shakeout_path),
        "--store",
        str(store),
    )
    for command, name, value in (
        ("record-attempt", "attempt.json", dataclasses.asdict(attempts[0])),
        ("record-outcome", "outcome.json", dataclasses.asdict(outcomes[0])),
    ):
        input_path = write(name, value)
        run(
            command,
            "--store",
            str(store),
            "--input",
            str(input_path),
        )
    inventory_path = write(
        "inventory.json",
        {
            field.name: sorted(getattr(inventory, field.name))
            for field in dataclasses.fields(inventory)
        },
    )
    historical_path = write("historical.json", {"status": "SYNTHETIC_CONTROL"})
    output = tmp_path / "report.json"
    run(
        "report",
        "--store",
        str(store),
        "--lineage-inventory",
        str(inventory_path),
        "--historical-report",
        str(historical_path),
        "--as-of-utc",
        "2026-01-02T00:00:00Z",
        "--bootstrap-seed",
        "8",
        "--output",
        str(output),
    )
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "INSUFFICIENT_EVIDENCE"
    assert report["panel_state"] == "COLLECTING"
