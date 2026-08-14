"""Verify the complete travel-time-v1.2 Milestone 4 gate."""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq  # type: ignore[import-untyped]
from arrive90_data_contracts.gates import DEFAULT_ACCEPTANCE_VERSION
from arrive90_evaluation.final_artifacts import (
    evaluation_code_sha256,
    file_sha256,
    value_sha256,
)
from arrive90_evaluation.final_evaluation import EVALUATION_SOURCE_NAMES
from arrive90_evaluation.year_dataset import YearDatasetError

ROOT = Path(__file__).resolve().parents[1]
FAILED_RUNTIME = ROOT / "artifacts/runtime/milestone-4"
RUNTIME = ROOT / "artifacts/runtime/milestone-4-recovery"
FINAL_REPORT_PATH = ROOT / "artifacts/reports/final/travel-time-v1.2.json"
CLAIMS_PATH = ROOT / "artifacts/reports/claims/travel-time-v1.2.json"
RECOVERY_REPORT_PATH = (
    ROOT / "artifacts/reports/qualification/milestone-4-access-recovery-v1.2.json"
)
QUALIFICATION_PATH = ROOT / "artifacts/reports/qualification/milestone-4-evaluation-v1.2.json"
GATE_PATH = ROOT / "artifacts/reports/gates/milestone-4.json"
DEMO_ROOT = ROOT / "artifacts/demo/travel-time-v1"
MODEL_ROOT = ROOT / "data/models/travel-time-v1/primary"
EXPECTED_MODELS = (
    "FULL-extreme-scale-1p0",
    "FULL-logistic-scale-1p0",
    "FULL-normal-scale-0p5",
    "INTERCEPT_ONLY-normal",
    "NO_POSITION_OBSERVATION-normal",
    "NO_PREFIX_HISTORY-normal",
    "SCHEDULE_CALENDAR-normal",
)
EXPECTED_SLICES = {
    "anchor_schedule_deviation_bucket",
    "day_type",
    "destination_class",
    "line_direction",
    "month",
    "observation_gap_bucket",
    "outcome_class",
    "peak_period",
    "platform_match_status",
    "scheduled_remaining_bucket",
    "season",
    "stop_sequence_match_status",
    "trip_match_status",
}
EXPECTED_OUTCOME_STATES = {
    "INTERVAL_RESOLVED",
    "LEFT_CENSORED",
    "MISSING_STOP_OBSERVATION",
    "NO_FOLLOW_UP",
    "OVER_WIDTH_INTERVAL",
    "RIGHT_CENSORED",
    "SCHEDULE_UNMATCHED",
    "SESSION_DISCONTINUITY",
}
DENOMINATOR_KEYS = {
    "analysis_weight",
    "distinct_anchor_count",
    "distinct_service_day_count",
    "raw_row_count",
}
FORBIDDEN_REPLAY_KEYS = {
    "anchor_latitude",
    "anchor_longitude",
    "latitude",
    "longitude",
    "source_row",
    "trip_id",
    "vehicle_id",
    "vehicle_label",
}


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise YearDatasetError(f"{path} must contain a JSON object")
    return payload


def _single(root: Path, pattern: str) -> Path:
    paths = sorted(root.glob(pattern))
    if len(paths) != 1:
        raise YearDatasetError(f"expected one {pattern} artifact under {root}")
    return paths[0]


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _is_denominator(value: object) -> bool:
    return isinstance(value, dict) and DENOMINATOR_KEYS.issubset(value)


def _metric_denominators_complete(summary: object) -> bool:
    if not isinstance(summary, dict):
        return False
    availability = summary.get("availability")
    horizons = summary.get("horizons")
    quantiles = summary.get("quantiles")
    if (
        not isinstance(availability, dict)
        or not all(_is_denominator(value) for value in availability.values())
        or not isinstance(horizons, list)
        or len(horizons) != 7
        or not isinstance(quantiles, list)
        or len(quantiles) != 3
    ):
        return False
    if not all(
        _is_denominator(item.get("identified")) and _is_denominator(item.get("unresolved"))
        for item in horizons
        if isinstance(item, dict)
    ):
        return False
    return all(
        _is_denominator(item.get("resolved_finite_upper"))
        and _is_denominator(item.get("unresolved_or_censored"))
        for item in quantiles
        if isinstance(item, dict)
    )


def _nested_replicates(value: object) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        if "replicates" in value and "lower_95" in value and "upper_95" in value:
            yield value
        for item in value.values():
            yield from _nested_replicates(item)
    elif isinstance(value, list):
        for item in value:
            yield from _nested_replicates(item)


def _contains_forbidden_key(value: object) -> bool:
    if isinstance(value, dict):
        return any(
            str(key).lower() in FORBIDDEN_REPLAY_KEYS or _contains_forbidden_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_forbidden_key(item) for item in value)
    return False


def _resolve_pointer(document: object, pointer: str) -> object:
    current = document
    for raw in pointer.removeprefix("/").split("/"):
        part = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            current = current[int(part)]
        elif isinstance(current, dict):
            current = current[part]
        else:
            raise KeyError(pointer)
    return current


def _build_recovery_report(
    failed_protocol_path: Path,
    failed_access_path: Path,
    recovery_protocol_path: Path,
    recovery_access_path: Path,
    prediction_manifest_path: Path,
) -> dict[str, Any]:
    failed_outputs = sorted(
        path.name
        for pattern in (
            "final-predictions-*.parquet",
            "final-prediction-manifest-*.json",
            "evaluation-run.json",
        )
        for path in FAILED_RUNTIME.glob(pattern)
    )
    return {
        "acceptance_version": DEFAULT_ACCEPTANCE_VERSION,
        "code_correction": (
            "encode non-finite bounds explicitly in the strict JSON row-hash payload while "
            "preserving numeric infinity in the prediction table"
        ),
        "failed_access_ledger_sha256": file_sha256(failed_access_path),
        "failed_attempt_evaluation_outputs": failed_outputs,
        "failed_attempt_produced_evaluation_output": bool(failed_outputs),
        "failed_protocol_sha256": value_sha256(_load_json(failed_protocol_path)),
        "metric_producing_attempt_count": 1,
        "prediction_manifest_sha256": value_sha256(_load_json(prediction_manifest_path)),
        "recovery_access_ledger_sha256": file_sha256(recovery_access_path),
        "recovery_attempt_count": 1,
        "recovery_protocol_sha256": value_sha256(_load_json(recovery_protocol_path)),
        "reported_failure": "Out of range float values are not JSON compliant: inf",
        "reported_failure_phase": "OPEN_FINAL_OUTCOMES_ROW_DIGEST",
        "version": "travel-time-final-access-recovery-v1",
    }


def build_reports() -> tuple[dict[str, Any], dict[str, Any]]:
    failed_protocol_path = _single(FAILED_RUNTIME, "evaluation-freeze-*.json")
    failed_access_path = FAILED_RUNTIME / "final-test-access.json"
    protocol_path = _single(RUNTIME, "evaluation-freeze-*.json")
    access_path = RUNTIME / "final-test-access.json"
    prediction_manifest_path = _single(RUNTIME, "final-prediction-manifest-*.json")
    rebuilt_report_path = RUNTIME / "rebuilt-report.json"
    runtime_report_path = RUNTIME / "evaluation-run.json"
    protocol = _load_json(protocol_path)
    access = _load_json(access_path)
    prediction_manifest = _load_json(prediction_manifest_path)
    runtime_report = _load_json(runtime_report_path)
    report = _load_json(FINAL_REPORT_PATH)
    claims = _load_json(CLAIMS_PATH)
    selection = _load_json(DEMO_ROOT / "replay-selection.json")
    fixture = _load_json(DEMO_ROOT / "replay-fixture.json")
    recovery = _build_recovery_report(
        failed_protocol_path,
        failed_access_path,
        protocol_path,
        access_path,
        prediction_manifest_path,
    )
    _write_json(RECOVERY_REPORT_PATH, recovery)

    prediction_path = RUNTIME / str(prediction_manifest["prediction_file"])
    metadata = pq.read_metadata(prediction_path)
    models = report.get("models")
    calibration = report.get("calibration")
    slices = report.get("slice_tables")
    availability = report.get("availability")
    if not isinstance(models, dict) or not isinstance(calibration, dict):
        raise YearDatasetError("final model or calibration report is invalid")
    if not isinstance(slices, dict) or not isinstance(availability, dict):
        raise YearDatasetError("final slice or availability report is invalid")
    promoted = models.get("FULL-normal-scale-0p5")
    if not isinstance(promoted, dict):
        raise YearDatasetError("promoted final model report is missing")
    expected_denominator = promoted["availability"]["all_selected"]
    model_denominators = [item["availability"]["all_selected"] for item in models.values()]
    calibration_complete = all(
        isinstance(rows, list)
        and len(rows) == 7
        and all(
            isinstance(item, dict)
            and isinstance(item.get("bins"), list)
            and all(
                _is_denominator(cell.get("identified")) and _is_denominator(cell.get("total"))
                for cell in item["bins"]
                if isinstance(cell, dict)
            )
            for item in rows
        )
        for rows in calibration.values()
    )
    slice_complete = all(
        isinstance(cells, dict)
        and bool(cells)
        and all(_metric_denominators_complete(summary) for summary in cells.values())
        for cells in slices.values()
    )
    outcome_counts = availability.get("selected_population", {})
    complete_counts = availability.get("complete_population_raw_counts", {})
    point = report.get("point_diagnostics", {})
    point_models = point.get("models", {}) if isinstance(point, dict) else {}
    point_complete = (
        all(
            _is_denominator(item.get("metric_eligible"))
            and _is_denominator(item.get("excluded_censored_or_unavailable"))
            for item in point_models.values()
            if isinstance(item, dict)
        )
        and len(point_models) == 3
    )
    intervals = list(_nested_replicates(report))
    claims_rows = claims.get("claims")
    final_report_sha256 = file_sha256(FINAL_REPORT_PATH)
    claims_bound = isinstance(claims_rows, list) and bool(claims_rows)
    if isinstance(claims_rows, list) and claims_rows:
        try:
            claims_bound = (
                all(
                    claim.get("artifact_sha256") == final_report_sha256
                    and _resolve_pointer(report, str(claim["report_pointer"])) is not None
                    for claim in claims_rows
                    if isinstance(claim, dict)
                )
                and len(claims_rows) == 5
            )
        except (KeyError, IndexError, ValueError):
            claims_bound = False

    source_root = ROOT / "packages/evaluation/src/arrive90_evaluation"
    current_evaluation_code_sha256 = evaluation_code_sha256(
        tuple(source_root / name for name in EVALUATION_SOURCE_NAMES), root=ROOT
    )
    model_manifest_sha256 = str(report["demo_artifacts"]["bundle_manifest_sha256"])
    source_bundle = MODEL_ROOT / "registry" / model_manifest_sha256
    demo_bundle = DEMO_ROOT / "model" / model_manifest_sha256
    bundle_files = ("calibration.json", "manifest.json", "model-manifest.json", "model.ubj")
    copied_bundle_identical = all(
        (source_bundle / name).read_bytes() == (demo_bundle / name).read_bytes()
        for name in bundle_files
    )
    demo_bundle_bytes = sum((demo_bundle / name).stat().st_size for name in bundle_files)
    selection_entries = selection.get("entries")
    replay_months = (
        [str(entry.get("month")) for entry in selection_entries]
        if isinstance(selection_entries, list)
        else []
    )
    prediction_columns = set(prediction_manifest.get("column_names", []))
    expected_model_columns = {
        f"model_{index:02d}__raw_margin" for index in range(len(EXPECTED_MODELS))
    }
    make = shutil.which("make")
    if make is None:
        raise RuntimeError("make is required for Milestone 4 qualification")
    check = subprocess.run(  # noqa: S603 - fixed local executable and arguments.
        [make, "check"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    checks = {
        "all_models_use_identical_rows_and_analysis_weights": (
            tuple(prediction_manifest.get("model_order", [])) == EXPECTED_MODELS
            and len(models) == 7
            and all(value == expected_denominator for value in model_denominators)
            and expected_model_columns.issubset(prediction_columns)
        ),
        "all_predeclared_metrics_slices_and_denominators_are_present": (
            set(slices) == EXPECTED_SLICES
            and slice_complete
            and all(_metric_denominators_complete(item) for item in models.values())
        ),
        "bootstrap_uses_exactly_2000_complete_service_day_blocks": (
            report.get("bootstrap", {}).get("replicates") == 2000
            and report.get("bootstrap", {}).get("service_day_block_count") == 61
            and bool(intervals)
            and all(
                item.get("replicates") == 2000
                and item.get("service_day_blocks") == 61
                and item.get("quantile_method") == "linear"
                for item in intervals
            )
        ),
        "calibration_and_reliability_tables_cover_every_model_and_horizon": (
            set(calibration) == set(EXPECTED_MODELS) and calibration_complete
        ),
        "committed_demo_bundle_is_exact_and_within_budget": (
            copied_bundle_identical
            and demo_bundle_bytes == report["demo_artifacts"]["bundle_bytes"]
            and demo_bundle_bytes <= 10 * 1024 * 1024
        ),
        "evaluation_protocol_binds_current_code_models_metrics_and_slices": (
            value_sha256(protocol) == protocol_path.stem.removeprefix("evaluation-freeze-")
            and protocol.get("evaluation_code_sha256") == current_evaluation_code_sha256
            and tuple(protocol.get("model_order", [])) == EXPECTED_MODELS
            and set(protocol.get("slice_dimensions", [])) == EXPECTED_SLICES
            and protocol.get("replay_selection_sha256")
            == prediction_manifest.get("replay_selection_sha256")
        ),
        "final_prediction_artifact_is_content_verified_and_identifier_hashed": (
            file_sha256(prediction_path) == prediction_manifest.get("prediction_sha256")
            and metadata.num_rows == prediction_manifest.get("row_count") == 199_364
            and prediction_manifest.get("final_test_outcomes_opened") is True
            and not {
                "anchor_observation_id",
                "example_id",
                "trip_id",
                "vehicle_id",
            }.intersection(prediction_columns)
        ),
        "final_report_rebuild_is_byte_identical": (
            file_sha256(rebuilt_report_path)
            == final_report_sha256
            == runtime_report.get("final_report_sha256")
        ),
        "interval_censoring_missing_and_quarantine_mass_is_reported": (
            set(outcome_counts) == EXPECTED_OUTCOME_STATES
            and set(complete_counts) == EXPECTED_OUTCOME_STATES
            and all(_is_denominator(value) for value in outcome_counts.values())
            and all(int(complete_counts[state]) > 0 for state in EXPECTED_OUTCOME_STATES)
            and int(availability.get("quarantined_raw_count", 0)) > 0
        ),
        "make_check_passed": check.returncode == 0,
        "negative_results_and_frozen_ablations_remain_visible": (
            report.get("negative_results", {}).get(
                "underperformance_is_retained_without_post_test_reselection"
            )
            is True
            and set(report.get("ablations", {}))
            == {"NO_POSITION_OBSERVATION-normal", "NO_PREFIX_HISTORY-normal"}
        ),
        "point_diagnostics_expose_eligible_and_excluded_censored_weight": point_complete,
        "public_claims_are_hash_bound_to_valid_report_pointers": (
            claims_bound and claims.get("final_report_sha256") == final_report_sha256
        ),
        "recovery_is_bounded_and_failed_attempt_produced_no_evaluation_output": (
            recovery["failed_attempt_produced_evaluation_output"] is False
            and recovery["recovery_attempt_count"] == 1
            and recovery["metric_producing_attempt_count"] == 1
            and access.get("access_count") == 1
            and runtime_report.get("final_test_access_count") == 1
            and recovery["failed_protocol_sha256"] != recovery["recovery_protocol_sha256"]
        ),
        "replay_fixture_is_outcome_blind_selected_redacted_and_bounded": (
            selection.get("final_test_outcomes_opened") is False
            and value_sha256(selection) == fixture.get("replay_selection_manifest_sha256")
            and fixture.get("replay_count") == len(selection_entries or []) == 200
            and replay_months.count("2024-11") == 100
            and replay_months.count("2024-12") == 100
            and not _contains_forbidden_key(fixture)
            and all(
                isinstance(entry, dict) and len(str(entry.get("source_example_sha256", ""))) == 64
                for entry in selection_entries or []
            )
        ),
    }
    failing = sorted(name for name, passed in checks.items() if not passed)
    observed = {
        "bootstrap_plan_sha256": report["bootstrap"]["plan_sha256"],
        "claim_count": len(claims_rows) if isinstance(claims_rows, list) else 0,
        "demo_bundle_bytes": demo_bundle_bytes,
        "failure_case_count": len(report.get("failure_cases", [])),
        "final_report_sha256": final_report_sha256,
        "model_interval_nll": {
            bundle_id: item["interval_negative_log_likelihood"]
            for bundle_id, item in models.items()
        },
        "point_diagnostics": point,
        "prediction_manifest_sha256": value_sha256(prediction_manifest),
        "prediction_row_count": prediction_manifest["row_count"],
        "recovery_report_sha256": file_sha256(RECOVERY_REPORT_PATH),
        "replay_count": fixture["replay_count"],
        "retained_outcome_mass": outcome_counts,
    }
    input_hashes = {
        "acceptance_charter": file_sha256(ROOT / "configs/acceptance/travel-time-v1.2.yaml"),
        "build_plan": file_sha256(ROOT / "BUILD_PLAN.md"),
        "claim_registry": file_sha256(CLAIMS_PATH),
        "evaluation_config": file_sha256(ROOT / "configs/evaluation/travel-time-v1.json"),
        "final_report": final_report_sha256,
        "prediction_file": file_sha256(prediction_path),
        "prediction_manifest": value_sha256(prediction_manifest),
        "protocol": value_sha256(protocol),
        "replay_fixture": file_sha256(DEMO_ROOT / "replay-fixture.json"),
        "uv_lock": file_sha256(ROOT / "uv.lock"),
    }
    git = shutil.which("git")
    implementation_commit = (
        subprocess.run(  # noqa: S603 - fixed local executable and arguments.
            [git, "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if git is not None
        else "unavailable"
    )
    environment = {
        "implementation_commit": implementation_commit,
        "machine": platform.machine(),
        "platform": platform.platform(),
        "python": platform.python_version(),
    }
    qualification: dict[str, Any] = {
        "acceptance_version": DEFAULT_ACCEPTANCE_VERSION,
        "checks": checks,
        "environment": environment,
        "failing_checks": failing,
        "input_manifest_hashes": input_hashes,
        "make_check_output_tail": (check.stdout + check.stderr)[-4_000:],
        "observed": observed,
        "qualification_command": "make qualify-milestone4",
        "state": "PASSED" if not failing else "FAILED",
    }
    _write_json(QUALIFICATION_PATH, qualification)
    gate: dict[str, Any] = {
        "acceptance_charter_sha256": input_hashes["acceptance_charter"],
        "acceptance_version": DEFAULT_ACCEPTANCE_VERSION,
        "checks": checks,
        "command": "make qualify-milestone4 && make gate MILESTONE=4",
        "environment": environment,
        "failing_checks": failing,
        "input_manifest_hashes": input_hashes,
        "milestone": 4,
        "observed": observed,
        "qualification_report_sha256": file_sha256(QUALIFICATION_PATH),
        "state": "ACCEPTED" if not failing else "FAILED",
    }
    _write_json(GATE_PATH, gate)
    return qualification, gate


def main() -> int:
    qualification, _gate = build_reports()
    print(QUALIFICATION_PATH.relative_to(ROOT))
    print(GATE_PATH.relative_to(ROOT))
    return 0 if qualification["state"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
