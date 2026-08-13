"""Write fail-closed Milestone 3 evidence from repository-owned inputs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from arrive90_features.registry import HISTORICAL_V1_REGISTRY

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
    milestone_two_path = ROOT / "artifacts/reports/gates/milestone-2.json"
    milestone_two = json.loads(milestone_two_path.read_text(encoding="utf-8"))
    features = list((ROOT / "packages/features/src").rglob("*.py"))
    outcomes = list((ROOT / "packages/outcomes/src").rglob("*.py"))
    tests = list((ROOT / "packages/features/tests").glob("test_*.py")) + list(
        (ROOT / "packages/outcomes/tests").glob("test_*.py")
    )
    checks = {
        "automatic_censoring_bounds_implemented": (
            ROOT / "packages/outcomes/src/arrive90_outcomes/bounds.py"
        ).is_file(),
        "baseline_framework_implemented": (
            ROOT / "packages/outcomes/src/arrive90_outcomes/baselines.py"
        ).is_file(),
        "feature_outcome_import_boundary_enforced": all(
            "arrive90_outcomes" not in path.read_text(encoding="utf-8") for path in features
        ),
        "full_candidate_resolution_gate_passed": False,
        "historical_v1_has_no_trip_update_prediction": all(
            spec.source.value != "TRIP_UPDATE_PREDICTION"
            for spec in HISTORICAL_V1_REGISTRY.specs.values()
        ),
        "interval_and_right_censored_aft_paths_implemented": (
            ROOT / "packages/outcomes/src/arrive90_outcomes/aft.py"
        ).is_file(),
        "milestone_2_accepted": milestone_two.get("status") == "PASSED",
        "primary_outcome_semantic_frozen": False,
        "required_baselines_fitted_on_frozen_training_population": False,
        "synthetic_feature_parity_and_leakage_tests_passed": True,
        "synthetic_virtual_rider_oracle_tests_passed": True,
        "virtual_rider_outcome_resolver_implemented": (
            ROOT / "packages/outcomes/src/arrive90_outcomes/oracle.py"
        ).is_file(),
    }
    failing = sorted(key for key, passed in checks.items() if not passed)
    return {
        "acceptance_version": "v1",
        "acceptance_version_hash": _digest(acceptance),
        "checks": checks,
        "command": "make check && make milestone3-evidence",
        "failing_checks": failing,
        "input_manifest_hashes": {
            "acceptance_charter": _digest(acceptance),
            "feature_registry": HISTORICAL_V1_REGISTRY.manifest_hash,
            "feature_schema_config": _digest(ROOT / "configs/features/historical-v1.yaml"),
            "features_implementation": _combined_digest(features),
            "milestone_2_report": _digest(milestone_two_path),
            "outcomes_implementation": _combined_digest(outcomes),
            "tests": _combined_digest(tests),
        },
        "milestone": 3,
        "status": "PASSED" if not failing else "INSUFFICIENT_EVIDENCE",
    }


def main() -> int:
    output = ROOT / "artifacts/reports/gates/milestone-3.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(build_report(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
