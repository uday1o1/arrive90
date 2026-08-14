"""Qualify the final Arrive90 documentation and portfolio evidence package."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_docs_assets import build_outputs  # noqa: E402
from scripts.build_public_claims import (  # noqa: E402
    public_claim_report_matches_current_evidence,
)

DOCUMENTS = (
    "README.md",
    "DATA_LICENSE.md",
    "docs/acceptance-charter.md",
    "docs/architecture.md",
    "docs/data-card.md",
    "docs/evaluation-report.md",
    "docs/ingestion.md",
    "docs/limitations.md",
    "docs/methodology.md",
    "docs/model-card.md",
    "docs/replay-demonstration.md",
    "docs/reproduction.md",
    "docs/source-feasibility.md",
    "docs/temporal-semantics.md",
)


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
    final_path = ROOT / "artifacts/reports/final/travel-time-v1.2.json"
    clean_path = ROOT / "artifacts/reports/qualification/clean-checkout-v1.2.json"
    audit_path = ROOT / "artifacts/reports/qualification/repository-audit-v1.2.json"
    claims_path = ROOT / "artifacts/reports/qualification/public-claims-v1.2.json"
    licenses_path = ROOT / "artifacts/reports/qualification/licenses-v1.json"
    robustness_path = ROOT / "artifacts/reports/qualification/milestone-6-robustness-v1.2.json"
    reproduction_path = ROOT / "artifacts/reports/qualification/milestone-6-reproduction-v1.2.json"
    final = _load(final_path)
    clean = _load(clean_path)
    audit = _load(audit_path)
    claims = _load(claims_path)
    licenses = _load(licenses_path)
    robustness = _load(robustness_path)
    reproduction = _load(reproduction_path)
    observations = clean.get("observations", {})
    chart_outputs = build_outputs(final)
    checks = {
        "accepted_full_year_reproduction_remains_valid": reproduction.get("status") == "PASSED",
        "all_documentation_charts_match_final_report": all(
            path.is_file() and path.read_text(encoding="utf-8") == body
            for path, body in chart_outputs.items()
        ),
        "all_required_documents_exist": all((ROOT / relative).is_file() for relative in DOCUMENTS),
        "browser_suite_passed_in_clean_checkout": observations.get("browser_tests_passed") == 4,
        "clean_reader_demo_and_terminal_passed": (
            clean.get("status") == "PASSED"
            and observations.get("demo_state") == "PASSED"
            and observations.get("terminal_manifest_reproduced") is True
        ),
        "license_and_attribution_audit_passed": licenses.get("status") == "PASSED",
        "public_claim_artifact_matches_current_evidence": (
            claims.get("status") == "PASSED"
            and public_claim_report_matches_current_evidence(claims)
        ),
        "python_quality_gate_passed_in_clean_checkout": (
            int(observations.get("python_tests_passed") or 0) > 0
            and float(observations.get("python_coverage_percent") or 0.0) >= 90.0
        ),
        "repository_audit_passed_from_clean_worktree": (
            audit.get("status") == "PASSED"
            and audit.get("checks", {}).get("worktree_is_clean") is True
        ),
        "robustness_suite_has_nine_passing_pairs": (
            robustness.get("status") == "PASSED"
            and len(robustness.get("scenarios", {})) == 9
            and observations.get("robustness_scenario_count") == 9
        ),
        "screenshot_and_walkthrough_are_present": (
            (ROOT / "artifacts/demos/replay-explorer.png").stat().st_size > 0
            and (ROOT / "artifacts/demos/replay-explorer-walkthrough.webm").stat().st_size > 0
        ),
    }
    return {
        "acceptance_version": "travel-time-v1.2",
        "checks": checks,
        "failing_checks": sorted(key for key, passed in checks.items() if not passed),
        "input_hashes": {
            "clean_checkout": _digest(clean_path),
            "documentation": _combined_digest([ROOT / relative for relative in DOCUMENTS]),
            "final_report": _digest(final_path),
            "licenses": _digest(licenses_path),
            "public_claims": _digest(claims_path),
            "repository_audit": _digest(audit_path),
            "reproduction": _digest(reproduction_path),
            "robustness": _digest(robustness_path),
            "screenshot": _digest(ROOT / "artifacts/demos/replay-explorer.png"),
            "walkthrough": _digest(ROOT / "artifacts/demos/replay-explorer-walkthrough.webm"),
        },
        "observed": {
            "browser_tests_passed": observations.get("browser_tests_passed"),
            "clean_checkout_commit": clean.get("commit"),
            "final_test_anchor_count": 36600,
            "final_test_row_count": final["final_test"]["row_count"],
            "final_test_service_day_count": 61,
            "promoted_bundle_id": "FULL-normal-scale-0p5",
            "promoted_interval_nll": final["models"]["FULL-normal-scale-0p5"][
                "interval_negative_log_likelihood"
            ],
            "python_coverage_percent": observations.get("python_coverage_percent"),
            "python_tests_passed": observations.get("python_tests_passed"),
            "robustness_pair_count": len(robustness.get("scenarios", {})),
        },
        "status": "PASSED" if all(checks.values()) else "FAILED",
        "version": "milestone-7-portfolio-package-v1.2",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts/reports/qualification/milestone-7-package-v1.2.json",
    )
    args = parser.parse_args()
    report = build_report()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(args.output)
    return 0 if report["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
