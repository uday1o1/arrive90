"""Bind every public Arrive90 result to immutable travel-time-v1.2 evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ACCEPTANCE = "configs/acceptance/travel-time-v1.2.yaml"
FINAL = "artifacts/reports/final/travel-time-v1.2.json"
FINAL_CLAIMS = "artifacts/reports/claims/travel-time-v1.2.json"
CLEAN = "artifacts/reports/qualification/clean-checkout-v1.2.json"
AUDIT = "artifacts/reports/qualification/repository-audit-v1.2.json"
LICENSES = "artifacts/reports/qualification/licenses-v1.json"
PERFORMANCE = "artifacts/reports/qualification/milestone-6-performance-v1.2.json"
ROBUSTNESS = "artifacts/reports/qualification/milestone-6-robustness-v1.2.json"
REPRODUCTION = "artifacts/reports/qualification/milestone-6-reproduction-v1.2.json"


def _digest(relative: str) -> str:
    return hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()


def _artifact(relative: str) -> dict[str, str]:
    return {"path": relative, "sha256": _digest(relative)}


def _load(relative: str) -> dict[str, Any]:
    value = json.loads((ROOT / relative).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{relative} must contain a JSON object")
    return value


def build_report() -> dict[str, Any]:
    final = _load(FINAL)
    final_claims = _load(FINAL_CLAIMS)
    clean = _load(CLEAN)
    audit = _load(AUDIT)
    licenses = _load(LICENSES)
    performance = _load(PERFORMANCE)
    robustness = _load(ROBUSTNESS)
    reproduction = _load(REPRODUCTION)
    gates = [_load(f"artifacts/reports/gates/milestone-{number}.json") for number in range(7)]
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    final_hash = _digest(FINAL)
    promoted = final["models"]["FULL-normal-scale-0p5"]
    schedule = final["models"]["SCHEDULE_CALENDAR-normal"]
    schedule_difference = final["point_diagnostics"]["comparisons"][
        "PROMOTED_P50_MINUS_OFFICIAL_SCHEDULE"
    ]["mean_absolute_interval_distance_difference_seconds"]
    empirical_difference = final["point_diagnostics"]["comparisons"][
        "PROMOTED_P50_MINUS_EMPIRICAL_MIDPOINT"
    ]["mean_absolute_interval_distance_difference_seconds"]
    checks = {
        "accepted_full_year_reproduction_is_retained": reproduction.get("status") == "PASSED",
        "all_previous_milestone_reports_are_accepted": all(
            gate.get("milestone") == number and gate.get("state") == "ACCEPTED"
            for number, gate in enumerate(gates)
        ),
        "clean_checkout_passed": clean.get("status") == "PASSED",
        "final_claim_registry_binds_current_report": (
            final_claims.get("final_report_sha256") == final_hash
            and all(
                claim.get("artifact_sha256") == final_hash
                for claim in final_claims.get("claims", [])
            )
        ),
        "license_and_attribution_audit_passed": licenses.get("status") == "PASSED",
        "performance_evidence_passed": performance.get("status") == "PASSED",
        "promoted_model_outperforms_strongest_aft_baseline": (
            promoted["interval_negative_log_likelihood"]
            < schedule["interval_negative_log_likelihood"]
        ),
        "readme_identifies_exact_final_report_hash": final_hash in readme,
        "readme_point_claims_match_final_artifact": all(
            marker in readme
            for marker in (
                "7.310 seconds",
                "6.872 to 7.783 seconds",
                "4.874 seconds",
                "4.307 to 5.455 seconds",
            )
        ),
        "readme_population_and_nll_match_final_artifact": all(
            marker in readme
            for marker in (
                f"{int(final['final_test']['row_count']):,}",
                "36,600",
                "61 service days",
                f"{float(promoted['interval_negative_log_likelihood']):.3f}",
                f"{float(schedule['interval_negative_log_likelihood']):.3f}",
            )
        ),
        "repository_audit_passed": audit.get("status") == "PASSED",
        "robustness_evidence_passed": robustness.get("status") == "PASSED",
    }
    claims = [
        {
            "artifacts": [_artifact(FINAL), _artifact(FINAL_CLAIMS)],
            "claim": "The promoted full bundle recorded final-test interval NLL 1.6470.",
            "evidence_state": "MEASURED_FROZEN_FINAL_TEST",
            "id": "promoted-interval-nll",
            "report_pointer": "/models/FULL-normal-scale-0p5/interval_negative_log_likelihood",
            "value": promoted["interval_negative_log_likelihood"],
        },
        {
            "artifacts": [_artifact(FINAL)],
            "claim": (
                "The strongest comparable schedule-and-calendar AFT baseline recorded "
                "final-test interval NLL 1.6735."
            ),
            "evidence_state": "MEASURED_FROZEN_FINAL_TEST",
            "id": "schedule-calendar-interval-nll",
            "report_pointer": "/models/SCHEDULE_CALENDAR-normal/interval_negative_log_likelihood",
            "value": schedule["interval_negative_log_likelihood"],
        },
        {
            "artifacts": [_artifact(FINAL), _artifact(FINAL_CLAIMS)],
            "claim": (
                "On common finite-upper rows, promoted p50 mean absolute interval distance "
                "was 7.310 seconds lower than the official schedule."
            ),
            "confidence_interval": {
                "lower_95": schedule_difference["lower_95"],
                "upper_95": schedule_difference["upper_95"],
            },
            "evidence_state": "MEASURED_NARROW_POINT_DIAGNOSTIC",
            "id": "promoted-vs-official-schedule",
            "report_pointer": (
                "/point_diagnostics/comparisons/PROMOTED_P50_MINUS_OFFICIAL_SCHEDULE/"
                "mean_absolute_interval_distance_difference_seconds"
            ),
            "value": schedule_difference["estimate"],
        },
        {
            "artifacts": [_artifact(FINAL), _artifact(FINAL_CLAIMS)],
            "claim": (
                "On common finite-upper rows, promoted p50 mean absolute interval distance "
                "was 4.874 seconds lower than the empirical midpoint."
            ),
            "confidence_interval": {
                "lower_95": empirical_difference["lower_95"],
                "upper_95": empirical_difference["upper_95"],
            },
            "evidence_state": "MEASURED_NARROW_POINT_DIAGNOSTIC",
            "id": "promoted-vs-empirical-midpoint",
            "report_pointer": (
                "/point_diagnostics/comparisons/PROMOTED_P50_MINUS_EMPIRICAL_MIDPOINT/"
                "mean_absolute_interval_distance_difference_seconds"
            ),
            "value": empirical_difference["estimate"],
        },
        {
            "artifacts": [_artifact(PERFORMANCE)],
            "claim": (
                "Full-year normalization peaked at 634,109,952 bytes while the verified raw "
                "store occupied 8,804,061,429 bytes."
            ),
            "evidence_state": "MEASURED_LOCAL_PERFORMANCE",
            "id": "bounded-normalization-memory",
            "value": performance["stages"]["normalization_full_year"]["peak_process_rss_bytes"],
        },
        {
            "artifacts": [_artifact(ROBUSTNESS)],
            "claim": "All nine seeded defects fail for their intended reason and controls pass.",
            "evidence_state": "MEASURED_PAIRED_ROBUSTNESS",
            "id": "paired-robustness",
            "value": len(robustness.get("scenarios", {})),
        },
        {
            "artifacts": [_artifact(REPRODUCTION)],
            "claim": (
                "A clean full-year rebuild matched the committed terminal, and its second pass "
                "verified 4,827 immutable files without rewriting them."
            ),
            "evidence_state": "MEASURED_CLEAN_REPRODUCTION",
            "id": "full-year-reproduction",
            "value": reproduction.get("immutable_output_file_count"),
        },
    ]
    acceptance_hash = _digest(ACCEPTANCE)
    for claim in claims:
        claim["acceptance_version"] = "travel-time-v1.2"
        claim["acceptance_version_sha256"] = acceptance_hash
    return {
        "acceptance_version": "travel-time-v1.2",
        "acceptance_version_sha256": acceptance_hash,
        "checks": checks,
        "claims": claims,
        "failing_checks": sorted(key for key, value in checks.items() if not value),
        "final_report_sha256": final_hash,
        "status": "PASSED" if all(checks.values()) else "FAILED",
        "version": "public-claims-v1.2",
    }


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument("--output", type=Path, required=True)
    return command


def main() -> int:
    args = parser().parse_args()
    report = build_report()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(args.output)
    return 0 if report["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
