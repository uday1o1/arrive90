"""Run and record the complete working-checkout Milestone 5 qualification."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/reports/qualification/milestone-5-working.json"
COMMANDS = (
    ("network_free_demo", ("make", "demo")),
    ("browser_workflow", ("make", "browser-test")),
    ("complete_quality_suite", ("make", "check")),
)


def _run(command: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed repository-owned command allow-list
        command,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def qualify() -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    observations: dict[str, Any] = {}
    for name, command in COMMANDS:
        completed = _run(command)
        result: dict[str, Any] = {
            "command": " ".join(command),
            "name": name,
            "return_code": completed.returncode,
        }
        if completed.returncode != 0:
            result["output_tail"] = (completed.stdout + completed.stderr)[-4_000:]
        results.append(result)
        if completed.returncode != 0:
            break
        if name == "network_free_demo":
            expected = ROOT / "artifacts/demo/travel-time-v1/terminal-manifest.json"
            actual = ROOT / "artifacts/runtime/demo-terminal-manifest.json"
            observations["terminal_manifest_reproduced"] = (
                expected.is_file()
                and actual.is_file()
                and expected.read_bytes() == actual.read_bytes()
            )
        elif name == "browser_workflow":
            browser = json.loads(
                (ROOT / "artifacts/runtime/playwright-results.json").read_text(encoding="utf-8")
            )
            observations["browser_expected"] = int(browser["stats"]["expected"])
            observations["browser_unexpected"] = int(browser["stats"]["unexpected"])
            observations["browser_titles"] = [
                spec["title"] for suite in browser["suites"] for spec in suite["specs"]
            ]
        elif name == "complete_quality_suite":
            combined = completed.stdout + completed.stderr
            matches = re.findall(r"(\d+) passed", combined)
            coverage = re.search(r"Total coverage: ([0-9.]+)%", combined)
            observations["python_tests_passed"] = int(matches[-1]) if matches else None
            observations["python_coverage_percent"] = float(coverage.group(1)) if coverage else None
    checks = {
        "all_working_checkout_commands_passed": (
            len(results) == len(COMMANDS) and all(result["return_code"] == 0 for result in results)
        ),
        "browser_workflows_passed": (
            observations.get("browser_expected") == 4
            and observations.get("browser_unexpected") == 0
        ),
        "terminal_manifest_reproduced": bool(observations.get("terminal_manifest_reproduced")),
    }
    return {
        "acceptance_version": "travel-time-v1.2",
        "checks": checks,
        "observations": observations,
        "results": results,
        "status": "PASSED" if all(checks.values()) else "FAILED",
        "version": "milestone-5-working-qualification-v1",
    }


def main() -> int:
    report = qualify()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(OUTPUT.relative_to(ROOT))
    return 0 if report["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
