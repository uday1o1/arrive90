"""Bind every public V1 claim to immutable repository evidence and acceptance hashes."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _digest(relative: str) -> str:
    return hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()


def _artifact(relative: str) -> dict[str, str]:
    return {"path": relative, "sha256": _digest(relative)}


def _load(relative: str) -> dict[str, Any]:
    value = json.loads((ROOT / relative).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{relative} must contain a JSON object")
    return value


def build_report() -> dict[str, Any]:
    acceptance_path = "configs/acceptance/v1.yaml"
    source_path = "artifacts/reports/gates/milestone-0.json"
    latency_path = "artifacts/reports/qualification/milestone-5-latency.json"
    performance_path = "artifacts/reports/qualification/milestone-6-performance.json"
    browser_path = "artifacts/reports/qualification/milestone-7-browser.json"
    prospective_path = "artifacts/reports/qualification/milestone-8-synthetic.json"
    security_path = "artifacts/reports/qualification/milestone-9-security.json"
    reliability_path = "artifacts/reports/qualification/milestone-9-reliability.json"
    licenses_path = "artifacts/reports/qualification/licenses-v1.json"
    clean_path = "artifacts/reports/qualification/clean-checkout-v1.json"
    audit_path = "artifacts/reports/qualification/repository-audit-v1.json"
    source = _load(source_path)
    latency = _load(latency_path)
    performance = _load(performance_path)
    browser = _load(browser_path)
    prospective = _load(prospective_path)
    security = _load(security_path)
    reliability = _load(reliability_path)
    licenses = _load(licenses_path)
    clean = _load(clean_path)
    audit = _load(audit_path)
    observations = clean.get("observations", {})
    checks = {
        "browser_claim_matches_clean_checkout": observations.get("browser_tests_passed") == 4,
        "clean_checkout_passed": clean.get("status") == "PASSED",
        "license_claim_passed": licenses.get("status") == "PASSED",
        "performance_claims_passed": (
            latency.get("status") == "PASSED" and performance.get("status") == "PASSED"
        ),
        "python_claim_matches_clean_checkout": (
            observations.get("python_tests_passed") == 234
            and observations.get("python_coverage_percent") == 91.2
        ),
        "reliability_claim_passed": reliability.get("status") == "PASSED",
        "repository_audit_passed": audit.get("status") == "PASSED",
        "security_claim_has_zero_finding_counts": (
            security.get("status") == "PASSED"
            and all(value == 0 for value in security.get("finding_counts", {}).values())
        ),
        "source_claim_remains_failed": source.get("status") == "FAILED",
        "synthetic_panel_claim_passed": prospective.get("status") == "PASSED",
        "synthetic_workflow_claim_passed": browser.get("status") == "PASSED",
    }
    acceptance_hash = _digest(acceptance_path)
    claims = [
        {
            "artifacts": [_artifact(source_path), _artifact(acceptance_path)],
            "claim": (
                "The public historical rail export cannot satisfy the primary Vehicle Position "
                "boarding-evidence contract."
            ),
            "evidence_state": "FAILED_SOURCE_GATE",
            "id": "source-provenance",
        },
        {
            "artifacts": [_artifact(clean_path)],
            "claim": (
                "The fresh-checkout workflow passes 234 Python tests at 91.20 percent coverage "
                "and four Chromium workflows."
            ),
            "evidence_state": "MEASURED_SOFTWARE_CORRECTNESS",
            "id": "clean-checkout-tests",
        },
        {
            "artifacts": [_artifact(reliability_path)],
            "claim": (
                "The targeted local fault, recovery, authorization, retention, backup, and "
                "redaction qualification passes 36 tests."
            ),
            "evidence_state": "MEASURED_LOCAL_FAULT_QUALIFICATION",
            "id": "reliability-tests",
        },
        {
            "artifacts": [_artifact(security_path)],
            "claim": (
                "The retained Trivy and Ruff qualification has zero critical or high "
                "vulnerabilities, misconfigurations, or secrets."
            ),
            "evidence_state": "DATED_LOCAL_SECURITY_SCAN",
            "id": "security-scan",
        },
        {
            "artifacts": [_artifact(latency_path), _artifact(performance_path)],
            "claim": (
                "The bounded ARM64 workload has a 7.095403 ms warm API p95 and a 0.172168 ms "
                "ten-candidate p95 on four cgroup CPUs and 8,307,167,232 memory bytes."
            ),
            "evidence_state": "MEASURED_BOUNDED_PERFORMANCE",
            "id": "bounded-performance",
        },
        {
            "artifacts": [_artifact(browser_path), _artifact(prospective_path)],
            "claim": (
                "The browser and 3,096-query prospective controls validate synthetic mechanics "
                "only."
            ),
            "evidence_state": "SYNTHETIC_ONLY",
            "id": "synthetic-controls",
        },
        {
            "artifacts": [_artifact(licenses_path), _artifact(audit_path)],
            "claim": (
                "Locked dependency licenses, attribution, tracked source, generated outputs, and "
                "publication boundaries have repository-owned audits."
            ),
            "evidence_state": "LOCAL_REPOSITORY_AUDIT",
            "id": "repository-audit",
        },
        {
            "artifacts": [
                _artifact(source_path),
                _artifact("artifacts/reports/gates/milestone-6.json"),
            ],
            "claim": "Arrive90 has no accepted MBTA reliability or calibration claim.",
            "evidence_state": "INSUFFICIENT_EVIDENCE",
            "id": "no-empirical-claim",
        },
    ]
    for claim in claims:
        claim["acceptance_version"] = "v1"
        claim["acceptance_version_sha256"] = acceptance_hash
    return {
        "acceptance_version": "v1",
        "acceptance_version_sha256": acceptance_hash,
        "checks": checks,
        "claims": claims,
        "failing_checks": sorted(key for key, value in checks.items() if not value),
        "status": "PASSED" if all(checks.values()) else "FAILED",
    }


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument("--output", type=Path, required=True)
    return command


def main() -> int:
    args = parser().parse_args()
    report = build_report()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(args.output)
    return 0 if report["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
