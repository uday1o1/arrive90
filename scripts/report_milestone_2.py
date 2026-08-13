"""Write fail-closed Milestone 2 evidence from repository-owned inputs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from arrive90_routing.graph_build import OTP_IMAGE

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
    milestone_one_path = ROOT / "artifacts/reports/gates/milestone-1.json"
    milestone_one = json.loads(milestone_one_path.read_text(encoding="utf-8"))
    synthetic_path = ROOT / "artifacts/reports/qualification/otp-synthetic-smoke.json"
    synthetic = json.loads(synthetic_path.read_text(encoding="utf-8"))
    routing_sources = list((ROOT / "packages/routing/src").rglob("*.py"))
    routing_tests = list((ROOT / "packages/routing/tests").glob("test_*.py"))
    connectivity = (ROOT / "configs/routing/connectivity-v1.yaml").read_text(encoding="utf-8")
    checks = {
        "candidate_contracts_implemented": (
            ROOT / "packages/data_contracts/src/arrive90_data_contracts/candidates.py"
        ).is_file(),
        "canonical_schedule_simulation_implemented": (
            ROOT / "packages/routing/src/arrive90_routing/simulation.py"
        ).is_file(),
        "complete_historical_graph_built": False,
        "frozen_scope_contains_at_least_100_pairs": False,
        "milestone_1_accepted": milestone_one.get("status") == "PASSED",
        "otp_image_digest_pinned": "@sha256:" in OTP_IMAGE,
        "query_population_generator_implemented": (
            ROOT / "packages/routing/src/arrive90_routing/population.py"
        ).is_file(),
        "recall_gates_measured_on_full_population": False,
        "static_audit_enumerator_implemented": (
            ROOT / "packages/routing/src/arrive90_routing/audit.py"
        ).is_file(),
        "synthetic_otp_graph_build_passed": synthetic.get("status") == "PASSED",
        "transfer_connectivity_frozen": "freeze_status: FROZEN" in connectivity,
    }
    failing = sorted(key for key, passed in checks.items() if not passed)
    return {
        "acceptance_version": "v1",
        "acceptance_version_hash": _digest(acceptance),
        "checks": checks,
        "command": "make check && make milestone2-evidence",
        "failing_checks": failing,
        "input_manifest_hashes": {
            "acceptance_charter": _digest(acceptance),
            "connectivity_rules": _digest(ROOT / "configs/routing/connectivity-v1.yaml"),
            "exceptional_trip_rules": _digest(ROOT / "configs/routing/exceptional-trips-v1.yaml"),
            "implementation": _combined_digest(routing_sources),
            "milestone_1_report": _digest(milestone_one_path),
            "otp_build_config": _digest(ROOT / "configs/otp/build-config.json"),
            "otp_router_config": _digest(ROOT / "configs/otp/router-config.json"),
            "synthetic_otp_qualification": _digest(synthetic_path),
            "tests": _combined_digest(routing_tests),
        },
        "milestone": 2,
        "status": "PASSED" if not failing else "INSUFFICIENT_EVIDENCE",
    }


def main() -> int:
    output = ROOT / "artifacts/reports/gates/milestone-2.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(build_report(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
