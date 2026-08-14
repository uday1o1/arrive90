"""Write the fail-closed Milestone 8 prospective-evidence gate report."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else None


def build_report() -> dict[str, Any]:
    acceptance = ROOT / "configs/acceptance/v1.yaml"
    config = ROOT / "configs/evaluation/prospective-v1.yaml"
    qualification_path = ROOT / "artifacts/reports/qualification/milestone-8-synthetic.json"
    source_path = ROOT / "artifacts/reports/gates/milestone-0.json"
    offline_path = ROOT / "artifacts/reports/gates/milestone-6.json"
    shakeout_path = ROOT / "artifacts/reports/prospective/shakeout-v1.json"
    prospective_path = ROOT / "artifacts/reports/prospective/prospective-v1.json"
    qualification = _load(qualification_path) or {}
    source = _load(source_path) or {}
    offline = _load(offline_path) or {}
    shakeout = _load(shakeout_path)
    prospective = _load(prospective_path)
    checks = {
        "local_protocol_mechanics_qualification_passed": qualification.get("status") == "PASSED",
        "source_feasibility_gate_passed": source.get("status") == "PASSED",
        "historical_v1_offline_gate_passed": offline.get("status") == "PASSED",
        "real_28_service_day_shakeout_passed": bool(
            shakeout
            and shakeout.get("status") == "PASSED"
            and shakeout.get("service_day_blocks", 0) >= 28
        ),
        "real_frozen_panel_report_present": prospective is not None,
        "real_frozen_panel_passed": bool(prospective and prospective.get("status") == "PASSED"),
        "real_panel_every_query_and_lineage_checks_passed": bool(
            prospective
            and prospective.get("checks", {}).get("every_scheduled_query_recorded_exactly_once")
            and prospective.get("checks", {}).get(
                "lineage_inventory_replays_every_retained_decision"
            )
        ),
        "real_panel_shadow_095_remained_nonserving": bool(
            prospective
            and prospective.get("policies", {}).get("shadow_095", {}).get("user_visible") is False
            and prospective.get("policies", {})
            .get("shadow_095", {})
            .get("current_acceptance_contribution")
            == "NONE"
        ),
    }
    failing = sorted(key for key, value in checks.items() if not value)
    input_hashes = {
        "acceptance_charter": _digest(acceptance),
        "milestone_0_report": _digest(source_path),
        "milestone_6_report": _digest(offline_path),
        "prospective_config": _digest(config),
        "synthetic_qualification": _digest(qualification_path),
    }
    if shakeout_path.is_file():
        input_hashes["real_shakeout_report"] = _digest(shakeout_path)
    if prospective_path.is_file():
        input_hashes["real_prospective_report"] = _digest(prospective_path)
    observed_failure = qualification.get("status") == "FAILED" or bool(
        (shakeout and shakeout.get("status") == "FAILED")
        or (prospective and prospective.get("status") == "FAILED")
    )
    status = "FAILED" if observed_failure else "PASSED" if not failing else "INSUFFICIENT_EVIDENCE"
    return {
        "acceptance_version": "v1",
        "acceptance_version_hash": _digest(acceptance),
        "checks": checks,
        "command": (
            "make check && make qualify-milestone8 && make milestone8-evidence "
            "&& make gate MILESTONE=8"
        ),
        "failing_checks": failing,
        "input_manifest_hashes": input_hashes,
        "local_work_package_status": "PASSED"
        if qualification.get("status") == "PASSED"
        else "FAILED",
        "milestone": 8,
        "missing_prerequisite": (
            "An accepted primary source gate and historical_v1 bundle are required before the "
            "28-service-day live shakeout can begin. After that shakeout passes, the fixed panel "
            "must collect and mature at least 56 additional service days."
        ),
        "prospective_claim_authorized": not failing,
        "resume_command": (
            "Satisfy artifacts/reports/gates/milestone-0.json, then follow "
            "docs/prospective-shadow.md beginning with the 28-service-day shakeout."
        ),
        "shadow_095_serving_eligible": False,
        "status": status,
    }


def main() -> int:
    output = ROOT / "artifacts/reports/gates/milestone-8.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(build_report(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
