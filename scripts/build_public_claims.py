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
LICENSES = "artifacts/reports/qualification/licenses-v1.json"
PERFORMANCE = "artifacts/reports/qualification/milestone-6-performance-v1.2.json"
ROBUSTNESS = "artifacts/reports/qualification/milestone-6-robustness-v1.2.json"
REPRODUCTION = "artifacts/reports/qualification/milestone-6-reproduction-v1.2.json"
PUBLIC_CLAIMS = "artifacts/reports/qualification/public-claims-v1.2.json"


def _digest(relative: str) -> str:
    return hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()


def _artifact(relative: str) -> dict[str, str]:
    return {"path": relative, "sha256": _digest(relative)}


def _load(relative: str) -> dict[str, Any]:
    value = json.loads((ROOT / relative).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{relative} must contain a JSON object")
    return value


def _readme_section(markdown: str, heading: str) -> str:
    marker = f"## {heading}\n"
    start = markdown.index(marker)
    end = markdown.find("\n## ", start + len(marker))
    return markdown[start : len(markdown) if end < 0 else end].rstrip()


def _find_horizon(model: dict[str, Any], seconds: int) -> dict[str, Any]:
    return next(item for item in model["horizons"] if item["horizon_seconds"] == seconds)


def _expected_measured_result(final: dict[str, Any], final_hash: str) -> str:
    promoted = final["models"]["FULL-normal-scale-0p5"]
    schedule = final["models"]["SCHEDULE_CALENDAR-normal"]
    population = promoted["availability"]["all_selected"]
    schedule_comparison = final["point_diagnostics"]["comparisons"][
        "PROMOTED_P50_MINUS_OFFICIAL_SCHEDULE"
    ]
    empirical_comparison = final["point_diagnostics"]["comparisons"][
        "PROMOTED_P50_MINUS_EMPIRICAL_MIDPOINT"
    ]
    schedule_difference = schedule_comparison["mean_absolute_interval_distance_difference_seconds"]
    empirical_difference = empirical_comparison[
        "mean_absolute_interval_distance_difference_seconds"
    ]
    promoted_point = final["point_diagnostics"]["models"]["PROMOTED_P50"]
    promoted_median = promoted_point["median_absolute_interval_distance_seconds"]
    brier = _find_horizon(promoted, 900)
    width = promoted["prediction_interval_width_seconds"]
    drift = final["drift"]["interval_nll_difference"]
    return "\n".join(
        (
            "## Measured result",
            "",
            (
                "The immutable final evaluation covers "
                f"{int(population['raw_row_count']):,} destination examples from "
                f"{int(population['distinct_anchor_count']):,} anchors across all "
                f"{int(population['distinct_service_day_count'])} service days in November "
                "and December 2024."
            ),
            (
                "The promoted `FULL-normal-scale-0p5` bundle achieved interval negative log "
                f"likelihood of {float(promoted['interval_negative_log_likelihood']):.3f}, "
                "compared with "
                f"{float(schedule['interval_negative_log_likelihood']):.3f} for the strongest "
                "schedule-and-calendar AFT baseline."
            ),
            "",
            (
                f"On the {int(schedule_comparison['common_rows']['raw_row_count']):,} common "
                "rows with finite upper bounds, the promoted p50 reduced mean absolute distance "
                "to the observed arrival interval by "
                f"{-float(schedule_difference['estimate']):.3f} seconds versus the official "
                "schedule, with a complete-service-day bootstrap 95 percent interval from "
                f"{-float(schedule_difference['upper_95']):.3f} to "
                f"{-float(schedule_difference['lower_95']):.3f} seconds."
            ),
            (
                "It reduced the same diagnostic by "
                f"{-float(empirical_difference['estimate']):.3f} seconds versus the empirical "
                "midpoint baseline, with a 95 percent interval from "
                f"{-float(empirical_difference['upper_95']):.3f} to "
                f"{-float(empirical_difference['lower_95']):.3f} seconds."
            ),
            "",
            "| Final-test measure | Promoted result | Evidence boundary |",
            "| --- | ---: | --- |",
            (
                "| Interval negative log likelihood | "
                f"{float(promoted['interval_negative_log_likelihood']):.3f} | "
                f"{int(population['raw_row_count']):,} held-out destination examples |"
            ),
            (
                "| p50 median absolute interval distance | "
                f"{float(promoted_median['estimate']):.3f} seconds | Finite-upper rows, 95% CI "
                f"{float(promoted_median['lower_95']):.3f} to "
                f"{float(promoted_median['upper_95']):.3f} |"
            ),
            (
                "| Identified 15-minute Brier score | "
                f"{float(brier['brier_identified']):.4f} | "
                f"{int(brier['identified']['raw_row_count']):,} identified rows |"
            ),
            (
                "| Mean p90 minus p50 width | "
                f"{float(width['mean']):.3f} seconds | All "
                f"{int(population['raw_row_count']):,} resolved predictions |"
            ),
            (
                "| December minus November NLL drift | "
                f"{float(drift):+.3f} | Descriptive temporal comparison |"
            ),
            "",
            (
                "Every value above maps to the "
                "[immutable final report](artifacts/reports/final/travel-time-v1.2.json) with "
                f"SHA-256 `{final_hash}`."
            ),
            (
                "The [machine-readable public claim audit]"
                f"({PUBLIC_CLAIMS}) derives every displayed value, denominator, confidence "
                "interval, and report pointer from that immutable report."
            ),
            "",
            "![Frozen model comparison](docs/assets/model-comparison.svg)",
        )
    )


README_CLAIM_POINTERS = {
    "aft-interval-nlls": (
        "/models/FULL-normal-scale-0p5/interval_negative_log_likelihood",
        "/models/SCHEDULE_CALENDAR-normal/interval_negative_log_likelihood",
        "/models/FULL-normal-scale-0p5/availability/all_selected",
    ),
    "empirical-midpoint-point-difference": (
        "/point_diagnostics/comparisons/PROMOTED_P50_MINUS_EMPIRICAL_MIDPOINT",
    ),
    "final-test-population": (
        "/models/FULL-normal-scale-0p5/availability/all_selected",
        "/final_test/start_date",
        "/final_test/end_date",
    ),
    "identified-15-minute-brier": ("/models/FULL-normal-scale-0p5/horizons/2",),
    "monthly-nll-drift": (
        "/drift/interval_nll_difference",
        "/drift/months/2024-11/metrics/availability/likelihood",
        "/drift/months/2024-12/metrics/availability/likelihood",
    ),
    "official-schedule-point-difference": (
        "/point_diagnostics/comparisons/PROMOTED_P50_MINUS_OFFICIAL_SCHEDULE",
    ),
    "p50-median-interval-distance": (
        "/point_diagnostics/models/PROMOTED_P50/median_absolute_interval_distance_seconds",
        "/point_diagnostics/models/PROMOTED_P50/metric_eligible",
    ),
    "prediction-width": (
        "/models/FULL-normal-scale-0p5/prediction_interval_width_seconds",
        "/models/FULL-normal-scale-0p5/availability/prediction_interval_resolved",
    ),
}


def _resolve_pointer(document: dict[str, Any], pointer: str) -> Any:
    value: Any = document
    for raw_token in pointer.removeprefix("/").split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        value = value[int(token)] if isinstance(value, list) else value[token]
    return value


def _readme_claim_map(final: dict[str, Any], final_hash: str) -> list[dict[str, Any]]:
    claims = [
        {
            "artifact_sha256": final_hash,
            "evidence": [
                {"report_pointer": pointer, "value": _resolve_pointer(final, pointer)}
                for pointer in pointers
            ],
            "id": claim_id,
        }
        for claim_id, pointers in README_CLAIM_POINTERS.items()
    ]
    claims.append(
        {
            "artifact_sha256": final_hash,
            "evidence": [{"report_pointer": "/", "value_sha256": final_hash}],
            "id": "immutable-final-report",
        }
    )
    return claims


def _readme_claim_map_is_exhaustive(
    final: dict[str, Any], final_hash: str, claims: list[dict[str, Any]]
) -> bool:
    expected_ids = {*README_CLAIM_POINTERS, "immutable-final-report"}
    if {claim.get("id") for claim in claims} != expected_ids:
        return False
    for claim in claims:
        if claim.get("artifact_sha256") != final_hash:
            return False
        evidence = claim.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            return False
        for item in evidence:
            pointer = item.get("report_pointer")
            if not isinstance(pointer, str):
                return False
            if pointer == "/":
                if item.get("value_sha256") != final_hash:
                    return False
            elif item.get("value") != _resolve_pointer(final, pointer):
                return False
    return True


def build_report() -> dict[str, Any]:
    final = _load(FINAL)
    final_claims = _load(FINAL_CLAIMS)
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
    expected_result_section = _expected_measured_result(final, final_hash)
    observed_result_section = _readme_section(readme, "Measured result")
    readme_claims = _readme_claim_map(final, final_hash)
    checks = {
        "accepted_full_year_reproduction_is_retained": reproduction.get("status") == "PASSED",
        "all_previous_milestone_reports_are_accepted": all(
            gate.get("milestone") == number and gate.get("state") == "ACCEPTED"
            for number, gate in enumerate(gates)
        ),
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
        "readme_measured_result_section_matches_artifact_exactly": (
            observed_result_section == expected_result_section
        ),
        "readme_public_claim_audit_is_exhaustive": _readme_claim_map_is_exhaustive(
            final, final_hash, readme_claims
        ),
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
        "input_hashes": {
            "final_claim_registry": _digest(FINAL_CLAIMS),
            "final_report": final_hash,
            "licenses": _digest(LICENSES),
            "performance": _digest(PERFORMANCE),
            "reproduction": _digest(REPRODUCTION),
            "robustness": _digest(ROBUSTNESS),
        },
        "readme_claims": readme_claims,
        "readme_measured_result_section_sha256": hashlib.sha256(
            observed_result_section.encode()
        ).hexdigest(),
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
