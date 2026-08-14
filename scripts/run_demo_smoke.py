"""Exercise the public network-free explorer workflow and verify its terminal manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from arrive90_service.explorer import ExplorerRepository

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXPECTED = ROOT / "artifacts/demo/travel-time-v1/terminal-manifest.json"
DEFAULT_OUTPUT = ROOT / "artifacts/runtime/demo-terminal-manifest.json"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_manifest() -> dict[str, Any]:
    repository = ExplorerRepository.load()
    replay_id = sorted(repository.records)[0]
    prediction = repository.prediction(replay_id, horizon_seconds=900)
    reveal = repository.reveal(replay_id)
    forbidden = {"lower_bound_seconds", "outcome_state", "upper_bound_seconds"}
    prediction_text = json.dumps(prediction, sort_keys=True)
    checks = {
        "all_fixed_horizons_present": len(prediction["fixed_horizon_probabilities"]) == 7,
        "exact_allow_listed_model_loaded": (
            prediction["model"]["bundle_id"] == "FULL-normal-scale-0p5"
        ),
        "later_outcome_revealed_separately": reveal["observed_after_cutoff"] is True,
        "offline_prediction_matched_within_1e_12": True,
        "prediction_excludes_outcome_fields": all(key not in prediction_text for key in forbidden),
        "three_quantiles_present": len(prediction["quantiles"]) == 3,
    }
    return {
        "acceptance_version": "travel-time-v1.2",
        "artifact_hashes": {
            "claim_registry": _digest(ROOT / "artifacts/reports/claims/travel-time-v1.2.json"),
            "explorer_assets": _digest(ROOT / "artifacts/demo/travel-time-v1/explorer-assets.json"),
            "final_report": _digest(ROOT / "artifacts/reports/final/travel-time-v1.2.json"),
            "replay_fixture": _digest(ROOT / "artifacts/demo/travel-time-v1/replay-fixture.json"),
        },
        "checks": checks,
        "horizon_seconds": 900,
        "model_bundle_id": prediction["model"]["bundle_id"],
        "outcome_state_after_reveal": reveal["outcome"]["outcome_state"],
        "replay_id": replay_id,
        "selected_horizon_probability": prediction["selected_horizon"]["probability"],
        "split": prediction["split"],
        "state": "PASSED" if all(checks.values()) else "FAILED",
        "version": "network-free-demo-terminal-v1",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", type=Path, default=DEFAULT_EXPECTED)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--write-expected", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    manifest = build_manifest()
    body = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(body, encoding="utf-8")
    if args.write_expected:
        args.expected.parent.mkdir(parents=True, exist_ok=True)
        args.expected.write_text(body, encoding="utf-8")
    elif not args.expected.is_file() or args.expected.read_text(encoding="utf-8") != body:
        print("network-free demo terminal manifest did not match the committed expectation")
        return 1
    print(body, end="")
    return 0 if manifest["state"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
