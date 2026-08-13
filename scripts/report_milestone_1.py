"""Write fail-closed Milestone 1 evidence from repository-owned inputs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from arrive90_ingestion.archive import ArchiveLimits
from arrive90_ingestion.collector import CollectorLimits

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
    milestone_zero_path = ROOT / "artifacts/reports/gates/milestone-0.json"
    milestone_zero = json.loads(milestone_zero_path.read_text(encoding="utf-8"))
    source_paths = list((ROOT / "packages/ingestion/src").rglob("*.py")) + list(
        (ROOT / "packages/data_contracts/src").rglob("*.py")
    )
    test_paths = list((ROOT / "packages/ingestion/tests").glob("test_*.py")) + list(
        (ROOT / "packages/data_contracts/tests").glob("test_*.py")
    )
    archive_limits = ArchiveLimits()
    collector_limits = CollectorLimits()
    checks = {
        "alert_revision_history_implemented": (
            ROOT / "packages/ingestion/src/arrive90_ingestion/alerts.py"
        ).is_file(),
        "archive_limits_frozen": archive_limits
        == ArchiveLimits(
            maximum_compressed_bytes=512 * 1024 * 1024,
            maximum_expanded_bytes=8 * 1024 * 1024 * 1024,
            maximum_expansion_ratio=64.0,
        ),
        "collector_limits_bounded": collector_limits.maximum_entities == 500_000
        and collector_limits.maximum_parse_seconds == 10.0,
        "evidence_provenance_normalization_implemented": (
            ROOT / "packages/ingestion/src/arrive90_ingestion/evidence.py"
        ).is_file(),
        "immutable_historical_storage_implemented": (
            ROOT / "packages/ingestion/src/arrive90_ingestion/historical.py"
        ).is_file(),
        "milestone_0_accepted": milestone_zero.get("status") == "PASSED",
        "primitive_vehicle_position_source_available": milestone_zero.get("checks", {}).get(
            "direct_vehicle_position_stop_provenance_available"
        )
        is True,
        "schedule_archive_cli_implemented": (
            ROOT / "packages/ingestion/src/arrive90_ingestion/schedule.py"
        ).is_file(),
        "temporal_view_implemented": (
            ROOT / "packages/ingestion/src/arrive90_ingestion/temporal.py"
        ).is_file(),
    }
    failing = sorted(key for key, passed in checks.items() if not passed)
    return {
        "acceptance_version": "v1",
        "acceptance_version_hash": _digest(acceptance),
        "checks": checks,
        "command": "make check && make milestone1-evidence",
        "failing_checks": failing,
        "input_manifest_hashes": {
            "acceptance_charter": _digest(acceptance),
            "implementation": _combined_digest(source_paths),
            "milestone_0_report": _digest(milestone_zero_path),
            "tests": _combined_digest(test_paths),
        },
        "milestone": 1,
        "status": "PASSED" if not failing else "INSUFFICIENT_EVIDENCE",
    }


def main() -> int:
    output = ROOT / "artifacts/reports/gates/milestone-1.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(build_report(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
