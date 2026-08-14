"""Copy and verify the frozen transform and point baseline for the local explorer."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO_ROOT = ROOT / "artifacts/demo/travel-time-v1"
TRANSFORM_SHA256 = "fb319bc0033c8ebdd34d5184d3b7ccb6a2d8ff1019a1cda605c3ca83574d6875"
BASELINE_SHA256 = "5ed5252cc5bbe11312de6d7d6d2736c972f5165d835f0b550f86aa80b869442d"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _copy_verified(source: Path, destination: Path, expected_sha256: str) -> None:
    if _sha256(source) != expected_sha256:
        raise ValueError(f"source artifact hash changed: {source}")
    if destination.exists() and destination.read_bytes() != source.read_bytes():
        raise ValueError(f"demo artifact conflicts with frozen source: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(source.read_bytes())


def main() -> int:
    transform = (
        ROOT
        / "data/datasets/travel-time-v1/transforms"
        / f"travel-time-transform-{TRANSFORM_SHA256}.json"
    )
    baseline = (
        ROOT / "data/models/travel-time-v1/primary" / f"point-baselines-{BASELINE_SHA256}.json"
    )
    transform_destination = DEMO_ROOT / "feature-transform.json"
    baseline_destination = DEMO_ROOT / "empirical-baseline.json"
    _copy_verified(transform, transform_destination, TRANSFORM_SHA256)
    _copy_verified(baseline, baseline_destination, BASELINE_SHA256)
    manifest = {
        "acceptance_version": "travel-time-v1.2",
        "empirical_baseline_path": baseline_destination.name,
        "empirical_baseline_sha256": BASELINE_SHA256,
        "feature_transform_path": transform_destination.name,
        "feature_transform_sha256": TRANSFORM_SHA256,
        "version": "travel-time-explorer-assets-v1",
    }
    manifest_path = DEMO_ROOT / "explorer-assets.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(manifest_path.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
