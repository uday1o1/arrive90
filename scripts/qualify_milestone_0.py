"""Run and freeze the complete travel-time-v1.1 Milestone 0 gate."""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
ACCEPTANCE_VERSION = "travel-time-v1.1"
PINNED_DATE = "2024-05-15"
QUALIFICATION_PATH = ROOT / "artifacts/reports/qualification/milestone-0-travel-time-v1.1.json"
GATE_PATH = ROOT / "artifacts/reports/gates/milestone-0.json"
ARRIVE90 = shutil.which("arrive90") or ""
GIT = shutil.which("git") or ""
MAKE = shutil.which("make") or ""
if not ARRIVE90 or not GIT or not MAKE:
    raise RuntimeError("arrive90, git, and make must be available on PATH")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return loaded


def _load_yaml(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return loaded


def _git(*args: str) -> str:
    process = subprocess.run(  # noqa: S603
        [GIT, *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return process.stdout.strip()


def _run_qualification(runtime_root: Path) -> dict[str, Any]:
    process = subprocess.run(  # noqa: S603
        [
            ARRIVE90,
            "data",
            "qualify-day",
            "--date",
            PINNED_DATE,
            "--runtime-root",
            str(runtime_root),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(process.stdout)
    if not isinstance(payload, dict):
        raise ValueError("qualification CLI must emit one JSON object")
    return payload


def _manifest(payload: dict[str, Any], field: str) -> bytes:
    path_value = payload.get(field)
    if not isinstance(path_value, str):
        raise ValueError(f"qualification payload is missing {field}")
    return Path(path_value).read_bytes()


def _gate_control(
    report_root: Path,
    name: str,
    body: bytes,
    expected_exit_code: int,
) -> dict[str, object]:
    path = report_root / "milestone-0.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    process = subprocess.run(  # noqa: S603
        [ARRIVE90, "gate", "--milestone", "0", "--report-root", str(report_root)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return {
        "expected_exit_code": expected_exit_code,
        "name": name,
        "observed_exit_code": process.returncode,
        "passed": process.returncode == expected_exit_code,
    }


def _gate_controls(root: Path) -> list[dict[str, object]]:
    base = {
        "acceptance_version": ACCEPTANCE_VERSION,
        "milestone": 0,
        "state": "ACCEPTED",
    }
    controls = [
        _gate_control(
            root / "accepted",
            "accepted",
            json.dumps(base).encode(),
            0,
        )
    ]
    controls.extend(
        [
            _gate_control(
                root / state.lower(),
                state.lower(),
                json.dumps({**base, "state": state}).encode(),
                1,
            )
            for state in ("NOT_STARTED", "IN_PROGRESS", "BLOCKED", "FAILED")
        ]
    )
    invalid = (
        ("missing_state", {key: value for key, value in base.items() if key != "state"}),
        ("unknown_state", {**base, "state": "UNKNOWN"}),
        ("legacy_status", {**base, "status": "PASSED"}),
        ("milestone_mismatch", {**base, "milestone": 1}),
        ("acceptance_mismatch", {**base, "acceptance_version": "legacy"}),
    )
    for name, payload in invalid:
        controls.append(_gate_control(root / name, name, json.dumps(payload).encode(), 1))
    controls.append(_gate_control(root / "malformed", "malformed", b"{", 1))
    return controls


def _combined_digest(paths: tuple[Path, ...]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.as_posix().encode()):
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_reports() -> tuple[dict[str, Any], dict[str, Any]]:
    check_process = subprocess.run(  # noqa: S603
        [MAKE, "check"],
        cwd=ROOT,
        check=False,
    )
    with tempfile.TemporaryDirectory(prefix="arrive90-m0-") as temporary:
        temp_root = Path(temporary)
        first = _run_qualification(temp_root / "first")
        second = _run_qualification(temp_root / "second")
        normalized_first = _manifest(first, "normalized_manifest_path")
        normalized_second = _manifest(second, "normalized_manifest_path")
        examples_first = _manifest(first, "example_manifest_path")
        examples_second = _manifest(second, "example_manifest_path")
        normalized = json.loads(normalized_first)
        examples = json.loads(examples_first)
        summary = _load_json(Path(str(first["run_summary_path"])))
        gate_controls = _gate_controls(temp_root / "gate-controls")

    charter_path = ROOT / "configs/acceptance/travel-time-v1.1.yaml"
    charter = _load_yaml(charter_path)
    timestamp_policy = charter.get("source_timestamp")
    if not isinstance(timestamp_policy, dict):
        raise ValueError("acceptance charter source_timestamp must be a mapping")
    raw_ignored = (
        subprocess.run(  # noqa: S603
            [GIT, "check-ignore", "-q", "data/raw"], cwd=ROOT, check=False
        ).returncode
        == 0
    )
    raw_status = _git("status", "--porcelain", "--", "data/raw")
    real_checks = summary.get("checks")
    if not isinstance(real_checks, list):
        raise ValueError("qualification summary checks must be a list")
    real_checks_by_name = {
        str(check["name"]): check
        for check in real_checks
        if isinstance(check, dict) and "name" in check
    }
    gate_control_passed = all(bool(control["passed"]) for control in gate_controls)
    checks = {
        "all_real_one_day_checks_passed": bool(summary.get("checks_passed")),
        "deduplicated_lineage_complete": bool(
            real_checks_by_name["deduplicated_lineage_complete"]["passed"]
        ),
        "finite_upper_evidence_is_later_same_episode_stop": bool(
            real_checks_by_name["finite_upper_evidence_integrity"]["passed"]
        ),
        "fresh_process_example_manifest_identical": examples_first == examples_second,
        "fresh_process_normalized_manifest_identical": normalized_first == normalized_second,
        "fresh_process_source_episode_assignment_identical": (
            normalized["episode_records_sha256"]
            == json.loads(normalized_second)["episode_records_sha256"]
        ),
        "gate_state_and_invalid_report_controls_passed": gate_control_passed,
        "make_check_passed": check_process.returncode == 0,
        "missing_destinations_never_finite": bool(
            real_checks_by_name["missing_destinations_never_finite"]["passed"]
        ),
        "pinned_schedule_join_measured": (
            examples["schedule_match_reason_counts"]
            and int(dict(examples["schedule_match_reason_counts"]).get("EXACT", 0)) > 0
        ),
        "raw_source_is_ignored_and_absent_from_status": raw_ignored and not raw_status,
        "schedule_publication_precedes_feature_cutoff": bool(
            real_checks_by_name["schedule_publication_before_feature_cutoff"]["passed"]
        ),
        "source_utc_policy_matches_alignment_discriminator": (
            timestamp_policy.get("input_semantics") == "NAIVE_UTC"
            and timestamp_policy.get("normalization") == "ATTACH_UTC_WITHOUT_CLOCK_ARITHMETIC"
            and timestamp_policy.get("schedule_alignment_probe_matches") == 13_260
            and timestamp_policy.get("schedule_alignment_naive_utc_median_seconds") == -88
            and timestamp_policy.get("schedule_alignment_naive_boston_median_seconds") == 14_312
        ),
        "feature_cutoff_and_future_access_guard_passed": bool(
            real_checks_by_name["feature_cutoff_and_future_access_guard"]["passed"]
        ),
    }
    failing = sorted(name for name, passed in checks.items() if not passed)
    environment = {
        "implementation_commit": _git("rev-parse", "HEAD"),
        "machine": platform.machine(),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
    }
    source_lock_paths = (
        ROOT / "configs/source-locks/mbta-2024.json",
        ROOT / "configs/source-locks/milestone0-acquired.json",
        ROOT / "configs/sources/bus-observatory-mbta-2024.yaml",
        ROOT / "configs/sources/mbta-gtfs-archive-2024.yaml",
    )
    implementation_paths = (
        ROOT / "packages/ingestion/src/arrive90_ingestion/vehicle.py",
        ROOT / "packages/ingestion/src/arrive90_ingestion/episodes.py",
        ROOT / "packages/ingestion/src/arrive90_ingestion/historical_schedule.py",
        ROOT / "packages/features/src/arrive90_features/travel_time.py",
        ROOT / "packages/outcomes/src/arrive90_outcomes/travel_time.py",
        ROOT / "packages/evaluation/src/arrive90_evaluation/travel_time_qualification.py",
        ROOT / "scripts/qualify_milestone_0.py",
    )
    input_hashes = {
        "acceptance_charter": _digest(charter_path),
        "build_plan": _digest(ROOT / "BUILD_PLAN.md"),
        "implementation": _combined_digest(implementation_paths),
        "source_locks": _combined_digest(source_lock_paths),
        "uv_lock": _digest(ROOT / "uv.lock"),
    }
    qualification = {
        "acceptance_version": ACCEPTANCE_VERSION,
        "checks": checks,
        "environment": environment,
        "example_manifest_sha256": first["example_manifest_sha256"],
        "failing_checks": failing,
        "fresh_process_gate_controls": gate_controls,
        "input_manifest_hashes": input_hashes,
        "normalized_manifest_sha256": first["normalized_manifest_sha256"],
        "observed": {
            "episode_count": normalized["episode_count"],
            "episode_support_by_route": normalized["episode_support_by_route"],
            "example_count": examples["example_count"],
            "example_metrics_by_route": examples["example_metrics_by_route"],
            "identity_availability_by_route": normalized["identity_availability_by_route"],
            "identity_availability_overall": normalized["identity_availability_overall"],
            "observation_count": normalized["observation_count"],
            "outcome_state_counts": examples["outcome_state_counts"],
            "quarantined_record_count": normalized["quarantined_record_count"],
            "retained_raw_row_count": normalized["retained_raw_row_count"],
            "schedule_match_reason_counts": examples["schedule_match_reason_counts"],
            "source_row_count": normalized["source_row_count"],
        },
        "qualification_command": "make qualify-milestone0",
        "source_date": PINNED_DATE,
        "state": "PASSED" if not failing else "FAILED",
    }
    gate = {
        "acceptance_charter_sha256": input_hashes["acceptance_charter"],
        "acceptance_version": ACCEPTANCE_VERSION,
        "checks": checks,
        "command": "make qualify-milestone0 && make gate MILESTONE=0",
        "environment": environment,
        "failing_checks": failing,
        "input_manifest_hashes": input_hashes,
        "milestone": 0,
        "observed": qualification["observed"],
        "qualification_report_sha256": "PENDING",
        "state": "ACCEPTED" if not failing else "IN_PROGRESS",
    }
    return qualification, gate


def main() -> int:
    qualification, gate = build_reports()
    _write(QUALIFICATION_PATH, qualification)
    gate["qualification_report_sha256"] = _digest(QUALIFICATION_PATH)
    _write(GATE_PATH, gate)
    print(QUALIFICATION_PATH.relative_to(ROOT))
    print(GATE_PATH.relative_to(ROOT))
    return 0 if gate["state"] == "ACCEPTED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
