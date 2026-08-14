"""Build the fail-closed travel-time-v1.2 Milestone 5 gate report."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from arrive90_service.explorer import HORIZONS, ExplorerRepository

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


def build_report() -> dict[str, Any]:
    working_path = ROOT / "artifacts/reports/qualification/milestone-5-working.json"
    clean_path = ROOT / "artifacts/reports/qualification/milestone-5-clean-demo.json"
    previous_path = ROOT / "artifacts/reports/gates/milestone-4.json"
    expected_path = ROOT / "artifacts/demo/travel-time-v1/terminal-manifest.json"
    screenshot_path = ROOT / "artifacts/demos/replay-explorer.png"
    video_path = ROOT / "artifacts/demos/replay-explorer-walkthrough.webm"
    working = _load(working_path)
    clean = _load(clean_path)
    previous = _load(previous_path)
    expected = _load(expected_path)
    repository = ExplorerRepository.load()
    replay_id = str(expected["replay_id"])
    prediction = repository.prediction(replay_id, horizon_seconds=900)
    reveal = repository.reveal(replay_id)
    prediction_body = json.dumps(prediction, sort_keys=True)
    browser_titles = set(working.get("observations", {}).get("browser_titles", []))
    source_paths = list((ROOT / "packages/service/src/arrive90_service").rglob("*.py"))
    source_paths.extend((ROOT / "packages/service/src/arrive90_service/web").glob("*.*"))
    test_paths = list((ROOT / "packages/service/tests").glob("test_*.py"))
    test_paths.append(ROOT / "tests/browser/rider-workflows.spec.js")
    checks = {
        "api_matches_frozen_offline_scorer": (
            prediction["selected_horizon"]["probability"]
            == expected["selected_horizon_probability"]
            and prediction["model"]["bundle_id"] == expected["model_bundle_id"]
        ),
        "browser_completes_selection_prediction_and_reveal": (
            "full replay selection, prediction, and outcome reveal is honest" in browser_titles
            and working.get("observations", {}).get("browser_unexpected") == 0
            and reveal["observed_after_cutoff"] is True
        ),
        "browser_exposes_fixed_horizons_quantiles_diagnostics_and_lineage": (
            len(prediction["fixed_horizon_probabilities"]) == len(HORIZONS)
            and len(prediction["quantiles"]) == 3
            and "fixed horizons, calibration diagnostics, and evidence remain visible"
            in browser_titles
        ),
        "clean_checkout_offline_demo_reproduces_terminal_manifest": (
            clean.get("status") == "PASSED"
            and clean.get("checks", {}).get("expected_terminal_manifest_reproduced") is True
            and clean.get("checks", {}).get("fresh_clone_remained_clean") is True
        ),
        "errors_are_specific_and_understandable": (
            "empty filtering and unsupported requests explain the problem" in browser_titles
        ),
        "keyboard_and_non_color_workflow_passes": (
            "primary workflow is keyboard reachable and never depends on color" in browser_titles
        ),
        "milestone_4_is_accepted": previous.get("state") == "ACCEPTED",
        "outcome_is_absent_from_prediction_and_feature_payload": (
            prediction["outcome_data_available_to_scorer"] is False
            and repository.fixture.get("feature_payload_excludes_outcomes") is True
            and all(
                "outcome_reveal" not in record.payload["feature_payload"]
                for record in repository.records.values()
            )
            and all(
                key not in prediction_body
                for key in ("lower_bound_seconds", "outcome_state", "upper_bound_seconds")
            )
        ),
        "truthful_screenshot_and_walkthrough_are_present": (
            screenshot_path.stat().st_size > 100_000 and video_path.stat().st_size > 50_000
        ),
        "working_checkout_demo_browser_and_check_pass": working.get("status") == "PASSED",
    }
    failing = sorted(key for key, passed in checks.items() if not passed)
    return {
        "acceptance_version": "travel-time-v1.2",
        "checks": checks,
        "command": "make qualify-milestone5 && make milestone5-evidence && make gate MILESTONE=5",
        "failing_checks": failing,
        "input_manifest_hashes": {
            "build_plan": _digest(ROOT / "BUILD_PLAN.md"),
            "clean_checkout_qualification": _digest(clean_path),
            "expected_terminal_manifest": _digest(expected_path),
            "milestone_4_report": _digest(previous_path),
            "replay_fixture": _digest(ROOT / "artifacts/demo/travel-time-v1/replay-fixture.json"),
            "screenshot": _digest(screenshot_path),
            "service_implementation": _combined_digest(source_paths),
            "tests": _combined_digest(test_paths),
            "walkthrough": _digest(video_path),
            "working_checkout_qualification": _digest(working_path),
        },
        "milestone": 5,
        "observed": {
            "browser_tests_passed": working["observations"]["browser_expected"],
            "clean_commit": clean["commit"],
            "model_bundle_id": prediction["model"]["bundle_id"],
            "python_coverage_percent": working["observations"]["python_coverage_percent"],
            "python_tests_passed": working["observations"]["python_tests_passed"],
            "replay_count": len(repository.records),
            "screenshot_bytes": screenshot_path.stat().st_size,
            "walkthrough_bytes": video_path.stat().st_size,
        },
        "state": "ACCEPTED" if not failing else "FAILED",
    }


def main() -> int:
    output = ROOT / "artifacts/reports/gates/milestone-5.json"
    report = build_report()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output.relative_to(ROOT))
    return 0 if report["state"] == "ACCEPTED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
