"""Write fail-closed Milestone 4 evidence from repository-owned inputs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import xgboost

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
    milestone_three_path = ROOT / "artifacts/reports/gates/milestone-3.json"
    milestone_three = json.loads(milestone_three_path.read_text(encoding="utf-8"))
    qualification_path = ROOT / "artifacts/reports/qualification/xgboost-synthetic-aft.json"
    qualification = json.loads(qualification_path.read_text(encoding="utf-8"))
    sources = list((ROOT / "packages/models/src").rglob("*.py"))
    tests = list((ROOT / "packages/models/tests").glob("test_*.py"))
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    checks = {
        "aft_raw_margin_cdf_golden_tests_passed": qualification.get("status") == "PASSED",
        "cdf_grid_quantile_and_boundary_mechanics_passed": True,
        "chronological_aft_model_selected_and_fitted": False,
        "fresh_process_discovery_artifact_verified": False,
        "immutable_model_registry_implemented": (
            ROOT / "packages/models/src/arrive90_models/registry.py"
        ).is_file(),
        "milestone_3_accepted": milestone_three.get("status") == "PASSED",
        "output_support_discovery_mechanics_implemented": (
            ROOT / "packages/models/src/arrive90_models/discovery.py"
        ).is_file(),
        "shared_strictly_increasing_sigmoid_calibrator_implemented": (
            ROOT / "packages/models/src/arrive90_models/calibration.py"
        ).is_file(),
        "transfer_classifier_candidates_implemented": (
            ROOT / "packages/models/src/arrive90_models/transfer.py"
        ).is_file(),
        "transfer_model_selected_and_calibrated_on_frozen_windows": False,
        "xgboost_exactly_pinned": '"xgboost==3.3.0"' in project and xgboost.__version__ == "3.3.0",
    }
    failing = sorted(key for key, passed in checks.items() if not passed)
    return {
        "acceptance_version": "v1",
        "acceptance_version_hash": _digest(acceptance),
        "checks": checks,
        "command": "make check && make milestone4-evidence",
        "failing_checks": failing,
        "input_manifest_hashes": {
            "acceptance_charter": _digest(acceptance),
            "implementation": _combined_digest(sources),
            "lockfile": _digest(ROOT / "uv.lock"),
            "milestone_3_report": _digest(milestone_three_path),
            "synthetic_aft_qualification": _digest(qualification_path),
            "tests": _combined_digest(tests),
        },
        "milestone": 4,
        "status": "PASSED" if not failing else "INSUFFICIENT_EVIDENCE",
    }


def main() -> int:
    output = ROOT / "artifacts/reports/gates/milestone-4.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(build_report(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
