"""Write the fail-closed Milestone 9 reliability, security, and publication gate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def build_report() -> dict[str, Any]:
    acceptance = ROOT / "configs/acceptance/v1.yaml"
    prior_paths = [ROOT / f"artifacts/reports/gates/milestone-{number}.json" for number in range(9)]
    prior = [_load(path) for path in prior_paths]
    evidence_paths = {
        "clean_checkout": ROOT / "artifacts/reports/qualification/clean-checkout-v1.json",
        "licenses": ROOT / "artifacts/reports/qualification/licenses-v1.json",
        "public_claims": ROOT / "artifacts/reports/qualification/public-claims-v1.json",
        "reliability": ROOT / "artifacts/reports/qualification/milestone-9-reliability.json",
        "repository_audit": ROOT / "artifacts/reports/qualification/repository-audit-v1.json",
        "security": ROOT / "artifacts/reports/qualification/milestone-9-security.json",
    }
    evidence = {name: _load(path) for name, path in evidence_paths.items()}
    policy = (ROOT / "deployment/release-policy.yaml").read_text(encoding="utf-8")
    local_checks = {
        "clean_checkout_passed": evidence["clean_checkout"].get("status") == "PASSED",
        "critical_and_high_security_findings_resolved": (
            evidence["security"].get("status") == "PASSED"
            and all(value == 0 for value in evidence["security"].get("finding_counts", {}).values())
        ),
        "failure_states_are_safe_and_recoverable": (
            evidence["reliability"].get("status") == "PASSED"
        ),
        "license_and_attribution_audit_passed": evidence["licenses"].get("status") == "PASSED",
        "public_claims_bind_immutable_artifacts": (
            evidence["public_claims"].get("status") == "PASSED"
        ),
        "repository_is_clean_and_required_source_is_tracked": (
            evidence["repository_audit"].get("status") == "PASSED"
        ),
        "retention_privacy_and_authorization_tests_passed": all(
            evidence["reliability"].get("checks", {}).get(key) is True
            for key in (
                "authorization_and_resource_bounds",
                "retention_and_expiration",
                "sensitive_observability_redaction",
            )
        ),
        "publication_requires_explicit_user_authorization": (
            "publication_requires_explicit_user_authorization: true" in policy
        ),
        "non_loopback_remains_prohibited_until_gate_passes": (
            "permitted_gate_status: PASSED" in policy
            and "plaintext_backend_publication_forbidden: true" in policy
        ),
    }
    prerequisite_checks = {
        f"milestone_{number}_accepted": report.get("status") == "PASSED"
        for number, report in enumerate(prior)
    }
    checks = {**local_checks, **prerequisite_checks}
    local_failed = [key for key, value in local_checks.items() if not value]
    status = (
        "FAILED" if local_failed else "PASSED" if all(checks.values()) else "INSUFFICIENT_EVIDENCE"
    )
    input_hashes = {
        "acceptance_charter": _digest(acceptance),
        **{name: _digest(path) for name, path in evidence_paths.items()},
        **{f"milestone_{number}_report": _digest(path) for number, path in enumerate(prior_paths)},
    }
    return {
        "acceptance_version": "v1",
        "acceptance_version_hash": _digest(acceptance),
        "checks": checks,
        "command": (
            "make check-all && make security-scan && make security-evidence "
            "&& make reliability-evidence && make license-evidence "
            "&& make public-claims-evidence && make milestone9-evidence "
            "&& make gate MILESTONE=9"
        ),
        "deployment_authorized": False,
        "failing_checks": sorted(key for key, value in checks.items() if not value),
        "input_manifest_hashes": input_hashes,
        "local_work_package_status": "PASSED" if not local_failed else "FAILED",
        "milestone": 9,
        "missing_prerequisite": (
            "Archived primitive Vehicle Position observations with separate stop provenance, "
            "stable identity, platform and status observations, observation timestamps, "
            "product-availability lineage, and per-train continuity are required to resume "
            "Milestone 0 and every dependent acceptance gate."
        ),
        "publication_authorized": False,
        "resume_command": (
            "make audit-source INDEX=$ARRIVE90_LAMP_INDEX PARQUET=$ARRIVE90_LAMP_PARQUET "
            "LAMP_ROOT=$ARRIVE90_LAMP_SOURCE LICENSE_PDF=$ARRIVE90_MASSDOT_LICENSE"
        ),
        "status": status,
    }


def main() -> int:
    output = ROOT / "artifacts/reports/gates/milestone-9.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(build_report(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
