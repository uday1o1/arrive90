"""Run paired Milestone 6 seeded-defect and nearby-control qualifications."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True, slots=True)
class Scenario:
    """One seeded defect and its closest positive control."""

    name: str
    defect_node: str
    control_node: str
    intended_reason: str


SCENARIOS = (
    Scenario(
        "interrupted_download_resume",
        "packages/ingestion/tests/test_acquisition.py::"
        "test_download_rejects_invalid_resume_status_range_and_final_size",
        "packages/ingestion/tests/test_acquisition.py::"
        "test_resumable_download_appends_only_a_valid_range_response",
        "invalid Content-Range is rejected while an exact byte range resumes",
    ),
    Scenario(
        "partial_object",
        "packages/ingestion/tests/test_acquisition.py::"
        "test_existing_download_rejects_wrong_size_and_digest",
        "packages/ingestion/tests/test_acquisition.py::"
        "test_existing_verified_download_is_reused_without_network",
        "partial or changed final bytes fail size and digest verification",
    ),
    Scenario(
        "changed_etag",
        "packages/ingestion/tests/test_acquisition.py::"
        "test_download_validates_expected_hash_and_response_metadata",
        "packages/ingestion/tests/test_acquisition.py::"
        "test_resumable_download_appends_only_a_valid_range_response",
        "changed ETag fails while the locked ETag and digest pass",
    ),
    Scenario(
        "schema_drift",
        "packages/ingestion/tests/test_vehicle.py::"
        "test_normalizer_rejects_missing_or_incompatible_physical_schema",
        "packages/ingestion/tests/test_vehicle.py::"
        "test_schema_validator_accepts_arrow_json_carriage_details",
        "missing, incompatible, and unknown columns fail while a supported optional type passes",
    ),
    Scenario(
        "malformed_parquet",
        "packages/ingestion/tests/test_acquisition.py::"
        "test_parquet_profile_rejects_malformed_or_partial_bytes",
        "packages/ingestion/tests/test_acquisition.py::"
        "test_parquet_profile_hashes_name_type_and_nullability",
        "malformed footer bytes fail while complete Parquet is profiled deterministically",
    ),
    Scenario(
        "duplicate_conflict",
        "packages/ingestion/tests/test_vehicle.py::"
        "test_normalizer_quarantines_conflicts_invalid_enums_and_missing_identity",
        "packages/ingestion/tests/test_vehicle.py::"
        "test_normalizer_filters_rail_attaches_utc_and_collapses_exact_duplicates",
        "conflicting identities quarantine while exact duplicates collapse with lineage",
    ),
    Scenario(
        "low_disk",
        "packages/ingestion/tests/test_acquisition.py::"
        "test_download_fails_before_network_when_disk_cannot_hold_remaining_bytes",
        "packages/ingestion/tests/test_acquisition.py::"
        "test_existing_verified_download_is_reused_without_network",
        "insufficient disk fails before network while verified immutable bytes are reused",
    ),
    Scenario(
        "corrupted_model",
        "packages/service/tests/test_explorer.py::test_corrupted_model_bytes_fail_before_scoring",
        "packages/service/tests/test_explorer.py::test_repository_verifies_exact_frozen_assets",
        "corrupted model bytes fail bundle validation before scoring",
    ),
    Scenario(
        "missing_artifact",
        "packages/service/tests/test_app.py::test_api_errors_are_specific_and_nonrevealing",
        "packages/service/tests/test_app.py::"
        "test_read_only_explorer_api_exercises_prediction_and_reveal",
        "missing explorer artifacts return a specific unavailable state while real assets serve",
    ),
)


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _correctness_hashes() -> dict[str, str]:
    return {
        "final_evaluation": _digest(ROOT / "artifacts/reports/final/travel-time-v1.2.json"),
        "replay_fixture": _digest(ROOT / "artifacts/demo/travel-time-v1/replay-fixture.json"),
        "terminal_manifest": _digest(ROOT / "artifacts/demo/travel-time-v1/terminal-manifest.json"),
    }


def _run_scenario(scenario: Scenario) -> dict[str, Any]:
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-o",
        "addopts=",
        "-q",
        scenario.defect_node,
        scenario.control_node,
    ]
    completed = subprocess.run(  # noqa: S603 - fixed interpreter and test allow-list
        command,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return {
        "control_node": scenario.control_node,
        "defect_node": scenario.defect_node,
        "intended_reason": scenario.intended_reason,
        "passed": completed.returncode == 0,
        "pytest_output_tail": (completed.stdout + completed.stderr)[-2000:],
        "return_code": completed.returncode,
    }


def build_report() -> dict[str, Any]:
    before = _correctness_hashes()
    scenarios = {scenario.name: _run_scenario(scenario) for scenario in SCENARIOS}
    after = _correctness_hashes()
    checks = {
        "all_nine_seeded_defects_fail_for_asserted_reason_and_controls_pass": (
            len(scenarios) == 9 and all(item["passed"] for item in scenarios.values())
        ),
        "qualification_preserves_correctness_artifacts": before == after,
    }
    return {
        "acceptance_version": "travel-time-v1.2",
        "checks": checks,
        "correctness_hashes": after,
        "failing_checks": sorted(key for key, passed in checks.items() if not passed),
        "scenarios": scenarios,
        "status": "PASSED" if all(checks.values()) else "FAILED",
        "version": "milestone-6-robustness-v1",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts/reports/qualification/milestone-6-robustness-v1.2.json",
    )
    args = parser.parse_args()
    report = build_report()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(args.output)
    return 0 if report["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
