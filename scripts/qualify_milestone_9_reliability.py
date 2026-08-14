"""Run the seeded failure, recovery, retention, authorization, and backup qualification."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TEST_FILES = (
    "packages/service/tests/test_app.py",
    "packages/service/tests/test_backup.py",
    "packages/service/tests/test_rate_limit.py",
    "packages/service/tests/test_reliability.py",
    "packages/service/tests/test_security_qualification.py",
    "packages/service/tests/test_store.py",
)
REQUIRED_TEST_MARKERS = {
    "authorization_and_resource_bounds": (
        "test_boundary_authorization_validation_and_rate_limits_fail_before_backend",
        "test_host_forwarded_transport_and_body_controls",
        "test_unauthorized_state_flood_cannot_consume_authorized_trip_budget",
    ),
    "backup_restore_and_tamper_rejection": (
        "test_backup_restore_is_create_only_integral_and_authorization_preserving",
        "test_restore_rejects_tampering_schema_and_overwrite",
    ),
    "clock_failure_and_recovery": (
        "test_clock_regression_and_malformed_trusted_forwarding_fail_closed",
    ),
    "database_and_sse_failure_recovery": (
        "test_database_failure_is_constant_shape_and_nonrevealing",
        "test_sse_database_fault_releases_stream_slot_and_recovers",
    ),
    "retention_and_expiration": (
        "test_running_lifespan_deletes_expired_trip_state",
        "test_expiry_cleanup_and_stop_delete_authority",
    ),
    "router_source_and_model_failure_recovery": (
        "test_source_and_model_failures_use_named_schedule_fallback",
        "test_router_failure_is_stable_and_does_not_fabricate_a_route",
    ),
    "secret_scan_seeded_defect_and_control": (
        "test_clean_security_evidence_passes",
        "test_seeded_high_vulnerability_fails_for_intended_reason",
    ),
    "sensitive_observability_redaction": (
        "test_wired_access_audit_excludes_inputs_and_trip_identifiers",
        "test_observability_sink_failure_does_not_break_service",
    ),
}


def _combined_digest(paths: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    for relative in paths:
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update((ROOT / relative).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def build_report() -> dict[str, Any]:
    sources = {relative: (ROOT / relative).read_text(encoding="utf-8") for relative in TEST_FILES}
    marker_checks = {
        key: all(any(marker in source for source in sources.values()) for marker in markers)
        for key, markers in REQUIRED_TEST_MARKERS.items()
    }
    command = [sys.executable, "-m", "pytest", "--no-cov", *TEST_FILES]
    completed = subprocess.run(  # noqa: S603 - exact active Python and fixed test paths
        command,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    checks = {"targeted_fault_suite_passed": completed.returncode == 0, **marker_checks}
    return {
        "checks": checks,
        "command": "uv run --no-sync python -m pytest --no-cov " + " ".join(TEST_FILES),
        "failing_checks": sorted(key for key, value in checks.items() if not value),
        "status": "PASSED" if all(checks.values()) else "FAILED",
        "stderr": completed.stderr.strip(),
        "stdout_tail": completed.stdout.strip().splitlines()[-8:],
        "test_source_sha256": _combined_digest(TEST_FILES),
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
