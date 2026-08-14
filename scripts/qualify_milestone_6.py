"""Verify Milestone 6 mechanics in fresh processes and retain deterministic evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fresh_process_payload() -> bytes:
    process = subprocess.run(
        [sys.executable, "-m", "arrive90_evaluation.qualification"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return process.stdout.strip()


def build_report() -> dict[str, Any]:
    first = _fresh_process_payload()
    second = _fresh_process_payload()
    if first != second:
        raise RuntimeError("fresh-process evaluation did not reproduce byte for byte")
    payload: dict[str, Any] = json.loads(first)
    report = payload["report"]
    checks = {
        "bootstrap_uses_at_least_2000_complete_service_day_replicates": (
            report["uncertainty"]["replicates"] >= 2_000
            and report["uncertainty"]["method"] == "PAIRED_COMPLETE_SERVICE_DAY_BLOCK_BOOTSTRAP"
        ),
        "complete_population_censoring_bounds_present": bool(report["censoring_bounds"]),
        "fresh_process_discovery_and_evaluation_reproduce": first == second,
        "holm_family_and_pareto_curve_present": bool(report["hypotheses"])
        and bool(report["pareto_frontier"]),
        "immutable_protocol_hash_present": len(report["protocol_hash"]) == 64,
        "negative_evidence_forces_explorer_pivot": report["release_mode"] == "HISTORICAL_EXPLORER",
        "synthetic_fixture_cannot_pass_empirical_gate": report["gate"]["passed"] is False
        and report["evidence_kind"] == "SYNTHETIC_MECHANICS_ONLY",
    }
    return {
        "checks": checks,
        "config_hash": _digest(ROOT / "configs/evaluation/v1.yaml"),
        "failing_checks": sorted(key for key, passed in checks.items() if not passed),
        "fresh_process_payload_sha256": hashlib.sha256(first).hexdigest(),
        **payload,
    }


def _write_immutable(path: Path, report: dict[str, Any]) -> None:
    content = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != content:
            raise FileExistsError(f"refusing to replace differing qualification report: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = build_report()
    _write_immutable(args.output, report)
    print(args.output)
    return 0 if not report["failing_checks"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
