"""Write fail-closed Milestone 6 evidence and the local evaluation card."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _combined_digest(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.as_posix().encode()):
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def build_report() -> dict[str, Any]:
    acceptance = ROOT / "configs/acceptance/v1.yaml"
    evaluation_config = ROOT / "configs/evaluation/v1.yaml"
    decision_policy = ROOT / "configs/decisions/v1.yaml"
    source_path = ROOT / "artifacts/reports/gates/milestone-0.json"
    milestone_five_path = ROOT / "artifacts/reports/gates/milestone-5.json"
    qualification_path = ROOT / "artifacts/reports/qualification/milestone-6-synthetic.json"
    performance_path = ROOT / "artifacts/reports/qualification/milestone-6-performance.json"
    source = json.loads(source_path.read_text(encoding="utf-8"))
    milestone_five = json.loads(milestone_five_path.read_text(encoding="utf-8"))
    qualification = json.loads(qualification_path.read_text(encoding="utf-8"))
    performance = json.loads(performance_path.read_text(encoding="utf-8"))
    decision_text = decision_policy.read_text(encoding="utf-8")
    local_checks = {
        "candidate_api_and_replay_benchmarks_passed": performance.get("status") == "PASSED",
        "complete_population_evaluation_mechanics_qualified": not qualification.get(
            "failing_checks"
        ),
        "evaluation_protocol_manifest_present": evaluation_config.is_file(),
        "explorer_pivot_activates_when_no_bundle_passes": (
            qualification.get("report", {}).get("release_mode") == "HISTORICAL_EXPLORER"
        ),
        "fresh_process_synthetic_reproduction_passed": qualification.get("checks", {}).get(
            "fresh_process_discovery_and_evaluation_reproduce"
        )
        is True,
        "immutable_machine_readable_report_present": qualification_path.is_file(),
    }
    acceptance_checks = {
        "empirical_final_test_comparisons_completed": False,
        "final_output_support_cells_passed": False,
        "final_test_outcomes_opened_under_frozen_protocol": False,
        "learned_or_model_free_bundle_passed_primary_gate": False,
        "milestone_5_accepted": milestone_five.get("status") == "PASSED",
        "primary_source_gate_accepted": source.get("status") == "PASSED",
        "production_eligibility_and_discovery_hashes_frozen": (
            "eligibility_manifest_hash: null" not in decision_text
            and "fixed_point_discovery_artifact_hash: null" not in decision_text
        ),
    }
    checks = {**local_checks, **acceptance_checks}
    failing = sorted(key for key, passed in checks.items() if not passed)
    return {
        "acceptance_version": "v1",
        "acceptance_version_hash": _digest(acceptance),
        "checks": checks,
        "command": (
            "make check && make qualify-milestone6 && make milestone6-evidence "
            "&& make gate MILESTONE=6"
        ),
        "failing_checks": failing,
        "input_manifest_hashes": {
            "acceptance_charter": _digest(acceptance),
            "decision_policy": _digest(decision_policy),
            "evaluation_config": _digest(evaluation_config),
            "implementation": _combined_digest(
                list((ROOT / "packages/evaluation/src").rglob("*.py"))
            ),
            "milestone_5_report": _digest(milestone_five_path),
            "performance_qualification": _digest(performance_path),
            "source_report": _digest(source_path),
            "synthetic_qualification": _digest(qualification_path),
            "tests": _combined_digest(list((ROOT / "packages/evaluation/tests").glob("test_*.py"))),
        },
        "milestone": 6,
        "missing_prerequisite": (
            "Archived primitive Vehicle Position observations with independent provenance are "
            "required before production hashes can freeze and final-test outcomes can open."
        ),
        "resume_command": ("make audit-source INDEX=... PARQUET=... LAMP_ROOT=... LICENSE_PDF=..."),
        "status": "PASSED" if not failing else "INSUFFICIENT_EVIDENCE",
    }


def build_card(gate: dict[str, Any]) -> dict[str, Any]:
    qualification = json.loads(
        (ROOT / "artifacts/reports/qualification/milestone-6-synthetic.json").read_text(
            encoding="utf-8"
        )
    )
    performance = json.loads(
        (ROOT / "artifacts/reports/qualification/milestone-6-performance.json").read_text(
            encoding="utf-8"
        )
    )
    return {
        "acceptance_status": gate["status"],
        "accepted_reliability_claim": None,
        "evidence_kind": "SYNTHETIC_MECHANICS_ONLY",
        "evaluation_protocol": "OFFLINE_EVALUATION_V1",
        "limitations": [
            gate["missing_prerequisite"],
            "Synthetic qualification does not estimate MBTA reliability or policy benefit.",
            "Prospective live calibration remains pending.",
        ],
        "local_mechanics": {
            "bootstrap_replicates": qualification["report"]["uncertainty"]["replicates"],
            "candidate_generation_p95_ms_max": max(
                result["p95_ms"] for result in performance["candidate_generation"].values()
            ),
            "fresh_process_payload_sha256": qualification["fresh_process_payload_sha256"],
            "performance_status": performance["status"],
            "release_mode": qualification["report"]["release_mode"],
            "replay_one_year_p95_ms": performance["replay_generation"]["one_year"]["p95_ms"],
        },
        "public_claim": (
            "Arrive90 implements reproducible offline evaluation mechanics but has insufficient "
            "empirical evidence for a reliability recommendation claim."
        ),
        "version": "milestone-6-local-mechanics-v1",
    }


def _write_card(path: Path, card: dict[str, Any]) -> None:
    content = json.dumps(card, indent=2, sort_keys=True) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") != content:
        raise FileExistsError("the versioned evaluation card already exists with different content")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(content, encoding="utf-8")


def main() -> int:
    report = build_report()
    output = ROOT / "artifacts/reports/gates/milestone-6.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _write_card(ROOT / "artifacts/cards/milestone-6-local-mechanics-v1.json", build_card(report))
    print(output.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
