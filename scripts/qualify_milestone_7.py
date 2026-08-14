"""Distill the full Playwright run into immutable Milestone 7 browser evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TESTS = (
    "direct target-not-met trip remains actionable and text-complete",
    "normalization, keyboard landmarks, and no-map use are visible",
    "stale, abstained, sparse, unsupported-target, and future branches stay explicit",
    "transfer trip exposes selected uncertainty and schedule-only recovery",
)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _titles(suites: list[dict[str, Any]]) -> tuple[str, ...]:
    titles: list[str] = []
    for suite in suites:
        titles.extend(spec["title"] for spec in suite.get("specs", []))
        titles.extend(_titles(suite.get("suites", [])))
    return tuple(sorted(titles, key=str.encode))


def build_report(source: Path) -> dict[str, Any]:
    raw = json.loads(source.read_text(encoding="utf-8"))
    titles = _titles(raw.get("suites", []))
    stats = raw.get("stats", {})
    screenshot = ROOT / "artifacts/demos/milestone-7-synthetic-ui.png"
    checks = {
        "all_declared_browser_workflows_present": titles == EXPECTED_TESTS,
        "chromium_run_has_no_flaky_result": stats.get("flaky") == 0,
        "chromium_run_has_no_skipped_result": stats.get("skipped") == 0,
        "chromium_run_has_no_unexpected_result": stats.get("unexpected") == 0,
        "exactly_four_workflows_passed": stats.get("expected") == 4,
        "playwright_exactly_pinned": raw.get("config", {}).get("version") == "1.61.0",
        "synthetic_demonstration_captured": screenshot.is_file(),
    }
    return {
        "browser": "chromium",
        "checks": checks,
        "evidence_kind": "SYNTHETIC_INTERFACE_WORKFLOW",
        "failing_checks": sorted(key for key, passed in checks.items() if not passed),
        "input_hashes": {
            "application_css": _digest(ROOT / "packages/service/src/arrive90_service/web/app.css"),
            "application_html": _digest(
                ROOT / "packages/service/src/arrive90_service/web/index.html"
            ),
            "application_javascript": _digest(
                ROOT / "packages/service/src/arrive90_service/web/app.js"
            ),
            "browser_spec": _digest(ROOT / "tests/browser/rider-workflows.spec.js"),
            "package_lock": _digest(ROOT / "package-lock.json"),
            "playwright_config": _digest(ROOT / "playwright.config.js"),
            "synthetic_demonstration": _digest(screenshot),
        },
        "playwright_version": raw.get("config", {}).get("version"),
        "status": "PASSED" if all(checks.values()) else "FAILED",
        "test_count": stats.get("expected"),
        "test_titles": list(titles),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = build_report(args.input)
    content = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output.exists():
        if args.output.read_text(encoding="utf-8") != content:
            raise FileExistsError("refusing to replace a differing browser qualification report")
        print(args.output)
        return 0 if report["status"] == "PASSED" else 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(content, encoding="utf-8")
    print(args.output)
    return 0 if report["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
