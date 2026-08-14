"""Run and record the complete local Milestone 6 quality suite."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def build_report() -> dict[str, Any]:
    completed = subprocess.run(
        ["make", "check"],  # noqa: S607 - fixed repository-owned command
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    combined = completed.stdout + completed.stderr
    passed = re.findall(r"(\d+) passed", combined)
    coverage = re.search(r"Total coverage: ([0-9.]+)%", combined)
    format_passed = "files already formatted" in combined
    lint_passed = "All checks passed!" in combined
    mypy_passed = "Success: no issues found" in combined
    observations = {
        "coverage_percent": float(coverage.group(1)) if coverage else None,
        "format_passed": format_passed,
        "lint_passed": lint_passed,
        "mypy_passed": mypy_passed,
        "python_tests_passed": int(passed[-1]) if passed else None,
        "return_code": completed.returncode,
    }
    checks = {
        "coverage_meets_repository_floor": (
            observations["coverage_percent"] is not None
            and observations["coverage_percent"] >= 90.0
        ),
        "format_lint_and_strict_typing_pass": format_passed and lint_passed and mypy_passed,
        "make_check_passes": completed.returncode == 0,
        "python_suite_passes": (
            observations["python_tests_passed"] is not None
            and observations["python_tests_passed"] >= 499
        ),
    }
    report: dict[str, Any] = {
        "acceptance_version": "travel-time-v1.2",
        "checks": checks,
        "failing_checks": sorted(key for key, value in checks.items() if not value),
        "observations": observations,
        "status": "PASSED" if all(checks.values()) else "FAILED",
        "version": "milestone-6-local-quality-v1",
    }
    if completed.returncode != 0:
        report["output_tail"] = combined[-4_000:]
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts/reports/qualification/milestone-6-local-v1.2.json",
    )
    args = parser.parse_args()
    report = build_report()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(args.output)
    return 0 if report["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
