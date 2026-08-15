"""Build the fail-closed travel-time-v1.2 Milestone 7 acceptance report."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def _qualification_environment(clean: dict[str, Any]) -> dict[str, Any]:
    environment = dict(clean.get("environment", {}))
    environment["implementation_commit"] = clean.get("commit")
    return environment


def build_report() -> dict[str, Any]:
    charter_path = ROOT / "configs/acceptance/travel-time-v1.2.yaml"
    source_lock_path = ROOT / "configs/source-locks/mbta-2024-acquired.json"
    dataset_path = ROOT / "artifacts/reports/qualification/milestone-2-dataset-v1.2.json"
    model_path = ROOT / "artifacts/reports/qualification/milestone-3-model-v1.2.json"
    final_path = ROOT / "artifacts/reports/final/travel-time-v1.2.json"
    claim_registry_path = ROOT / "artifacts/reports/claims/travel-time-v1.2.json"
    previous_path = ROOT / "artifacts/reports/gates/milestone-6.json"
    qualification_path = ROOT / "artifacts/reports/qualification/milestone-7-package-v1.2.json"
    clean_path = ROOT / "artifacts/reports/qualification/clean-checkout-v1.2.json"
    audit_path = ROOT / "artifacts/reports/qualification/repository-audit-v1.2.json"
    claims_path = ROOT / "artifacts/reports/qualification/public-claims-v1.2.json"
    tracker_path = ROOT / "configs/acceptance/travel-time-v1.2-milestones.json"
    previous = _load(previous_path)
    qualification = _load(qualification_path)
    clean = _load(clean_path)
    audit = _load(audit_path)
    claims = _load(claims_path)
    tracker = _load(tracker_path)
    tracked_states = {
        int(item["milestone"]): item["state"] for item in tracker.get("milestones", [])
    }
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    checks = {
        "all_milestone_reports_zero_through_six_are_accepted": (
            previous.get("state") == "ACCEPTED"
            and all(
                _load(ROOT / f"artifacts/reports/gates/milestone-{number}.json").get("state")
                == "ACCEPTED"
                for number in range(7)
            )
            and all(tracked_states.get(number) == "ACCEPTED" for number in range(7))
        ),
        "final_tracker_postcondition_is_accepted": all(
            tracked_states.get(number) == "ACCEPTED" for number in range(8)
        ),
        "clean_reader_demo_is_documented_and_passed": (
            clean.get("status") == "PASSED"
            and clean.get("checks", {}).get("network_free_demo_terminal_reproduced") is True
        ),
        "complete_quality_browser_robustness_and_reproduction_evidence_passes": (
            qualification.get("status") == "PASSED"
            and qualification.get("checks", {}).get("accepted_full_year_reproduction_remains_valid")
            is True
            and qualification.get("checks", {}).get("robustness_suite_has_nine_passing_pairs")
            is True
        ),
        "every_readme_result_matches_immutable_evidence": (
            claims.get("status") == "PASSED"
            and qualification.get("checks", {}).get(
                "public_claim_artifact_matches_current_evidence"
            )
            is True
        ),
        "publication_and_deployment_targets_remain_absent": all(
            target not in makefile
            for target in (
                "publish:",
                "deploy:",
                "release:",
                "open-pr:",
            )
        ),
        "repository_audit_has_no_stale_or_unexplained_state": (
            audit.get("status") == "PASSED"
            and audit.get("checks", {}).get("no_stale_public_scope_claim") is True
            and audit.get("checks", {}).get("worktree_is_clean") is True
        ),
        "source_attribution_and_noncommercial_notice_are_audited": audit.get("checks", {}).get(
            "source_attribution_and_noncommercial_notice_present"
        )
        is True,
    }
    failing = sorted(key for key, passed in checks.items() if not passed)
    docs = [
        ROOT / "README.md",
        ROOT / "DATA_LICENSE.md",
        *sorted((ROOT / "docs").glob("*.md")),
        *sorted((ROOT / "docs/assets").glob("*.svg")),
    ]
    implementation_and_tests = [
        ROOT / "Makefile",
        ROOT / "scripts/audit_repository.py",
        ROOT / "scripts/build_public_claims.py",
        ROOT / "scripts/qualify_clean_checkout.py",
        ROOT / "scripts/qualify_milestone_7.py",
        ROOT / "scripts/report_milestone_7.py",
        ROOT / "packages/evaluation/tests/test_portfolio_audits.py",
    ]
    return {
        "acceptance_charter_sha256": _digest(charter_path),
        "acceptance_version": "travel-time-v1.2",
        "checks": checks,
        "command": (
            "make check-all && make qualify-milestone6-robustness && "
            "make qualify-milestone7 && make milestone7-evidence && make gate MILESTONE=7"
        ),
        "failing_checks": failing,
        "input_manifest_hashes": {
            "acceptance_charter": _digest(charter_path),
            "build_plan": _digest(ROOT / "BUILD_PLAN.md"),
            "claim_registry": _digest(claim_registry_path),
            "clean_checkout": _digest(clean_path),
            "code_and_tests": _combined_digest(implementation_and_tests),
            "dataset_qualification": _digest(dataset_path),
            "documentation_and_charts": _combined_digest(docs),
            "final_report": _digest(final_path),
            "milestone_6_report": _digest(previous_path),
            "model_qualification": _digest(model_path),
            "portfolio_qualification": _digest(qualification_path),
            "public_claims": _digest(claims_path),
            "repository_audit": _digest(audit_path),
            "source_lock": _digest(source_lock_path),
            "tracker": _digest(tracker_path),
        },
        "environment": _qualification_environment(clean),
        "milestone": 7,
        "observed": qualification.get("observed", {}),
        "qualified_commit": clean.get("commit"),
        "state": "ACCEPTED" if not failing else "FAILED",
    }


def main() -> int:
    output = ROOT / "artifacts/reports/gates/milestone-7.json"
    report = build_report()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output.relative_to(ROOT))
    return 0 if report["state"] == "ACCEPTED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
