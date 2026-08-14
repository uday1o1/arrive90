"""Build the fail-closed travel-time-v1.2 Milestone 6 acceptance report."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _combined_digest(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.relative_to(ROOT).as_posix().encode()):
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def build_report() -> dict[str, Any]:
    previous_path = ROOT / "artifacts/reports/gates/milestone-5.json"
    performance_path = ROOT / "artifacts/reports/qualification/milestone-6-performance-v1.2.json"
    robustness_path = ROOT / "artifacts/reports/qualification/milestone-6-robustness-v1.2.json"
    reproduction_path = ROOT / "artifacts/reports/qualification/milestone-6-reproduction-v1.2.json"
    local_path = ROOT / "artifacts/reports/qualification/milestone-6-local-v1.2.json"
    expected_path = ROOT / "artifacts/reproduction/full-year-terminal.json"
    previous = _load(previous_path)
    performance = _load(performance_path)
    robustness = _load(robustness_path)
    reproduction = _load(reproduction_path)
    local = _load(local_path)
    scenarios = robustness.get("scenarios", {})
    performance_checks = performance.get("checks", {})
    reproduction_checks = reproduction.get("checks", {})
    peak = performance.get("stages", {}).get("normalization_full_year", {})
    checks = {
        "complete_local_verification_passes": local.get("status") == "PASSED",
        "every_seeded_defect_and_nearby_control_passes": (
            robustness.get("status") == "PASSED"
            and len(scenarios) == 9
            and all(item.get("passed") is True for item in scenarios.values())
        ),
        "milestone_5_demo_and_full_reproduction_terminals_pass": (
            previous.get("state") == "ACCEPTED"
            and reproduction.get("status") == "PASSED"
            and reproduction_checks.get("first_terminal_matches_committed_expectation") is True
            and reproduction_checks.get("second_terminal_matches_first_and_expectation") is True
            and reproduction.get("terminal_manifest_sha256") == _digest(expected_path)
        ),
        "no_op_rerun_verifies_manifests_without_rewrites": (
            reproduction_checks.get("no_op_rerun_verified_every_derived_stage") is True
            and reproduction_checks.get("no_op_rerun_did_not_rewrite_derived_outputs") is True
        ),
        "performance_measurement_preserves_correctness_outputs": (
            performance.get("status") == "PASSED"
            and performance_checks.get("benchmark_did_not_change_correctness_artifacts") is True
            and robustness.get("checks", {}).get("qualification_preserves_correctness_artifacts")
            is True
            and performance.get("optimization", {}).get("performed") is False
        ),
        "peak_memory_is_bounded_independent_of_full_archive_size": (
            performance_checks.get("full_year_peak_memory_is_bounded_below_70_percent_of_host")
            is True
            and int(peak.get("peak_process_rss_bytes", 0)) > 0
            and int(peak.get("peak_process_rss_bytes", 0))
            < int(performance.get("storage_bytes", {}).get("raw", 0))
        ),
        "resume_rejects_truncated_or_changed_bytes": all(
            scenarios.get(name, {}).get("passed") is True
            for name in ("changed_etag", "interrupted_download_resume", "partial_object")
        ),
    }
    failing = sorted(key for key, passed in checks.items() if not passed)
    implementation_paths = [
        ROOT / "packages/ingestion/src/arrive90_ingestion/acquisition.py",
        ROOT / "scripts/reproduce_full_year.py",
        ROOT / "scripts/qualify_milestone_6_local.py",
        ROOT / "scripts/qualify_milestone_6_reproduction.py",
        ROOT / "scripts/qualify_milestone_6_robustness.py",
        ROOT / "benchmarks/run_milestone6.py",
    ]
    test_paths = [
        ROOT / "packages/ingestion/tests/test_acquisition.py",
        ROOT / "packages/ingestion/tests/test_vehicle.py",
        ROOT / "packages/service/tests/test_app.py",
        ROOT / "packages/service/tests/test_explorer.py",
    ]
    return {
        "acceptance_version": "travel-time-v1.2",
        "checks": checks,
        "command": ("make qualify-milestone6 && make milestone6-evidence && make gate MILESTONE=6"),
        "failing_checks": failing,
        "input_manifest_hashes": {
            "build_plan": _digest(ROOT / "BUILD_PLAN.md"),
            "expected_terminal": _digest(expected_path),
            "implementation": _combined_digest(implementation_paths),
            "local_quality": _digest(local_path),
            "milestone_5_report": _digest(previous_path),
            "performance": _digest(performance_path),
            "reproduction": _digest(reproduction_path),
            "robustness": _digest(robustness_path),
            "tests": _combined_digest(test_paths),
        },
        "milestone": 6,
        "observed": {
            "clean_reproduction_commit": reproduction.get("commit"),
            "defect_control_pairs": len(scenarios),
            "full_year_normalization_peak_memory_bytes": peak.get("peak_process_rss_bytes"),
            "no_op_verified_file_count": reproduction.get("immutable_output_file_count"),
            "python_coverage_percent": local.get("observations", {}).get("coverage_percent"),
            "python_tests_passed": local.get("observations", {}).get("python_tests_passed"),
            "raw_storage_bytes": performance.get("storage_bytes", {}).get("raw"),
            "terminal_manifest_sha256": reproduction.get("terminal_manifest_sha256"),
        },
        "state": "ACCEPTED" if not failing else "FAILED",
    }


def main() -> int:
    output = ROOT / "artifacts/reports/gates/milestone-6.json"
    report = build_report()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output.relative_to(ROOT))
    return 0 if report["state"] == "ACCEPTED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
