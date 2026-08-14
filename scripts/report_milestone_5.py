"""Write fail-closed Milestone 5 evidence from repository-owned inputs."""

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
    policy = ROOT / "configs/decisions/v1.yaml"
    milestone_four_path = ROOT / "artifacts/reports/gates/milestone-4.json"
    milestone_four = json.loads(milestone_four_path.read_text(encoding="utf-8"))
    latency_path = ROOT / "artifacts/reports/qualification/milestone-5-latency.json"
    latency = json.loads(latency_path.read_text(encoding="utf-8"))
    source_report = json.loads(
        (ROOT / "artifacts/reports/gates/milestone-0.json").read_text(encoding="utf-8")
    )
    source_files = list((ROOT / "packages/decision/src").rglob("*.py")) + list(
        (ROOT / "packages/service/src").rglob("*.py")
    )
    test_files = list((ROOT / "packages/decision/tests").glob("test_*.py")) + list(
        (ROOT / "packages/service/tests").glob("test_*.py")
    )
    checks = {
        "api_and_sse_contracts_implemented": (
            ROOT / "packages/service/src/arrive90_service/app.py"
        ).is_file(),
        "decision_capability_and_trip_bearer_store_implemented": (
            ROOT / "packages/service/src/arrive90_service/store.py"
        ).is_file(),
        "deterministic_initial_and_recovery_kernels_implemented": (
            ROOT / "packages/decision/src/arrive90_decision/initial.py"
        ).is_file()
        and (ROOT / "packages/decision/src/arrive90_decision/recovery.py").is_file(),
        "empirical_recorded_scenarios_qualified": False,
        "frozen_named_hardware_latency_targets_passed": latency.get("status") == "PASSED",
        "milestone_4_accepted": milestone_four.get("status") == "PASSED",
        "primary_source_gate_accepted": source_report.get("status") == "PASSED",
        "production_model_and_support_bundle_available": False,
        "threat_model_has_no_open_local_critical_or_high_finding": (
            "No critical or high-severity finding remains open"
            in (ROOT / "docs/threat-model-milestone-5.md").read_text(encoding="utf-8")
        ),
        "versioned_decision_policy_manifest_present": policy.is_file(),
    }
    failing = sorted(key for key, passed in checks.items() if not passed)
    return {
        "acceptance_version": "v1",
        "acceptance_version_hash": _digest(acceptance),
        "checks": checks,
        "command": "make check && make milestone5-evidence && make gate MILESTONE=5",
        "failing_checks": failing,
        "input_manifest_hashes": {
            "acceptance_charter": _digest(acceptance),
            "benchmark_dockerfile": _digest(ROOT / "benchmarks/milestone5.Dockerfile"),
            "decision_policy": _digest(policy),
            "implementation": _combined_digest(source_files),
            "latency_qualification": _digest(latency_path),
            "lockfile": _digest(ROOT / "uv.lock"),
            "milestone_4_report": _digest(milestone_four_path),
            "tests": _combined_digest(test_files),
            "threat_model": _digest(ROOT / "docs/threat-model-milestone-5.md"),
        },
        "milestone": 5,
        "missing_prerequisite": (
            "An accepted Milestone 0 primitive Vehicle Position source and the resulting frozen "
            "chronological model, calibration, support, and final-test evidence are required."
        ),
        "resume_command": "make audit-source INDEX=... PARQUET=... LAMP_ROOT=... LICENSE_PDF=...",
        "status": "PASSED" if not failing else "INSUFFICIENT_EVIDENCE",
    }


def main() -> int:
    output = ROOT / "artifacts/reports/gates/milestone-5.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(build_report(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
