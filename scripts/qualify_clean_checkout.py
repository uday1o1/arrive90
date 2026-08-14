"""Verify the documented Arrive90 workflow from one exact remote commit."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
GIT: str = shutil.which("git") or ""
if not GIT:
    raise RuntimeError("git is required for clean-checkout qualification")

FULL_COMMANDS = (
    ("sync_python", ("uv", "sync", "--frozen", "--all-groups")),
    ("network_free_demo", ("make", "demo")),
    ("install_node", ("npm", "ci")),
    ("install_chromium", ("npx", "playwright", "install", "chromium")),
    ("local_and_browser_checks", ("make", "check-all")),
    (
        "robustness_qualification",
        (
            "uv",
            "run",
            "--no-sync",
            "python",
            "scripts/qualify_milestone_6_robustness.py",
            "--output",
            "artifacts/runtime/clean-checkout-robustness.json",
        ),
    ),
    (
        "license_audit",
        (
            "uv",
            "run",
            "--no-sync",
            "python",
            "scripts/audit_licenses.py",
            "--output",
            "artifacts/runtime/clean-checkout-licenses.json",
        ),
    ),
    (
        "repository_audit",
        (
            "uv",
            "run",
            "--no-sync",
            "python",
            "scripts/audit_repository.py",
            "--output",
            "artifacts/runtime/clean-checkout-repository-audit.json",
        ),
    ),
    *((f"milestone_{number}_gate", ("make", "gate", f"MILESTONE={number}")) for number in range(7)),
)
DEMO_COMMANDS = FULL_COMMANDS[:2]


def _run(
    command: tuple[str, ...], *, cwd: Path, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # noqa: S603 - fixed repository-owned command allow-list
            command,
            cwd=cwd,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as error:
        return subprocess.CompletedProcess(command, 127, "", str(error))


def _version(command: tuple[str, ...], *, cwd: Path, env: dict[str, str]) -> str:
    completed = _run(command, cwd=cwd, env=env)
    return completed.stdout.strip() or completed.stderr.strip()


def _load_optional(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _text_digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def qualify(*, repository: str, commit: str, workflow: str = "full") -> dict[str, Any]:
    with tempfile.TemporaryDirectory(
        prefix="arrive90-clean-checkout-", dir=ROOT.parent
    ) as temporary:
        clone = Path(temporary) / "repository"
        clone_process = subprocess.run(  # noqa: S603 - fixed Git clone operation
            [GIT, "clone", "--no-checkout", repository, str(clone)],
            check=False,
            capture_output=True,
            text=True,
        )
        results: list[dict[str, Any]] = [
            {
                "command": "git clone --no-checkout <repository>",
                "name": "clone",
                "stderr_sha256": _text_digest(clone_process.stderr),
                "status": clone_process.returncode,
                "stdout_sha256": _text_digest(clone_process.stdout),
            }
        ]
        if clone_process.returncode != 0:
            return {
                "checks": {"all_documented_commands_passed": False},
                "commit": commit,
                "failing_command": "clone",
                "repository": repository,
                "results": results,
                "status": "FAILED",
            }
        checkout = subprocess.run(  # noqa: S603 - fixed detached checkout in temporary clone
            [GIT, "checkout", "--detach", commit],
            cwd=clone,
            check=False,
            capture_output=True,
            text=True,
        )
        results.append(
            {
                "command": "git checkout --detach <commit>",
                "name": "checkout",
                "stderr_sha256": _text_digest(checkout.stderr),
                "status": checkout.returncode,
                "stdout_sha256": _text_digest(checkout.stdout),
            }
        )
        if checkout.returncode != 0:
            return {
                "checks": {"all_documented_commands_passed": False},
                "commit": commit,
                "failing_command": "checkout",
                "repository": repository,
                "results": results,
                "status": "FAILED",
            }
        env = dict(os.environ)
        env["UV_CACHE_DIR"] = str(clone / ".cache" / "uv")
        failing_command: str | None = None
        observations: dict[str, Any] = {}
        commands = DEMO_COMMANDS if workflow == "demo" else FULL_COMMANDS
        for name, command in commands:
            command_env = dict(env)
            if name == "network_free_demo":
                command_env["UV_OFFLINE"] = "1"
            completed = _run(command, cwd=clone, env=command_env)
            result: dict[str, Any] = {
                "command": " ".join(command),
                "name": name,
                "stderr_sha256": _text_digest(completed.stderr),
                "status": completed.returncode,
                "stdout_sha256": _text_digest(completed.stdout),
            }
            if completed.returncode != 0:
                result["stderr_tail"] = (completed.stdout + completed.stderr)[-4_000:]
            results.append(result)
            if completed.returncode != 0:
                failing_command = name
                break
            if name == "network_free_demo":
                manifest = _load_optional(clone / "artifacts/runtime/demo-terminal-manifest.json")
                expected = _load_optional(
                    clone / "artifacts/demo/travel-time-v1/terminal-manifest.json"
                )
                observations["demo_state"] = manifest.get("state")
                observations["terminal_manifest_reproduced"] = manifest == expected
            elif name == "local_and_browser_checks":
                passed_counts = [
                    int(value) for value in re.findall(r"(\d+) passed", completed.stdout)
                ]
                coverage = re.search(r"Total coverage: ([0-9.]+)%", completed.stdout)
                observations["python_tests_passed"] = passed_counts[0] if passed_counts else None
                observations["browser_tests_passed"] = (
                    passed_counts[-1] if len(passed_counts) > 1 else None
                )
                observations["python_coverage_percent"] = (
                    float(coverage.group(1)) if coverage else None
                )
            elif name == "robustness_qualification":
                robustness = _load_optional(
                    clone / "artifacts/runtime/clean-checkout-robustness.json"
                )
                observations["robustness_status"] = robustness.get("status")
                observations["robustness_scenario_count"] = len(robustness.get("scenarios", {}))
            elif name == "license_audit":
                observations["licenses_status"] = _load_optional(
                    clone / "artifacts/runtime/clean-checkout-licenses.json"
                ).get("status")
            elif name == "repository_audit":
                observations["repository_audit_status"] = _load_optional(
                    clone / "artifacts/runtime/clean-checkout-repository-audit.json"
                ).get("status")
        reproduction = _load_optional(
            clone / "artifacts/reports/qualification/milestone-6-reproduction-v1.2.json"
        )
        observations["accepted_reproduction_status"] = reproduction.get("status")
        observations["accepted_reproduction_file_count"] = reproduction.get(
            "immutable_output_file_count"
        )
        status = _run((GIT, "status", "--porcelain"), cwd=clone, env=env)
        head = _version((GIT, "rev-parse", "HEAD"), cwd=clone, env=env)
        checks = {
            "all_documented_commands_passed": failing_command is None,
            "exact_commit_checked_out": head == commit,
            "fresh_clone_remained_clean": status.returncode == 0 and not status.stdout.strip(),
            "network_free_demo_terminal_reproduced": bool(
                observations.get("terminal_manifest_reproduced")
            ),
        }
        if workflow == "full":
            checks.update(
                {
                    "accepted_full_year_reproduction_evidence_present": (
                        observations.get("accepted_reproduction_status") == "PASSED"
                        and observations.get("accepted_reproduction_file_count") == 4827
                    ),
                    "browser_workflows_passed": observations.get("browser_tests_passed") == 4,
                    "python_quality_gate_passed": (
                        int(observations.get("python_tests_passed") or 0) > 0
                        and float(observations.get("python_coverage_percent") or 0.0) >= 90.0
                    ),
                    "repository_and_license_audits_passed": (
                        observations.get("repository_audit_status") == "PASSED"
                        and observations.get("licenses_status") == "PASSED"
                    ),
                    "robustness_pairs_passed": (
                        observations.get("robustness_status") == "PASSED"
                        and observations.get("robustness_scenario_count") == 9
                    ),
                }
            )
        return {
            "acceptance_version": "travel-time-v1.2",
            "checks": checks,
            "commit": commit,
            "environment": {
                "machine": platform.machine(),
                "node": _version(("node", "--version"), cwd=clone, env=env),
                "npm": _version(("npm", "--version"), cwd=clone, env=env),
                "platform": platform.platform(),
                "python": _version(
                    (str(clone / ".venv" / "bin" / "python"), "--version"),
                    cwd=clone,
                    env=env,
                ),
                "uv": _version(("uv", "--version"), cwd=clone, env=env),
            },
            "failing_command": failing_command,
            "observations": observations,
            "repository": repository,
            "results": results,
            "status": "PASSED" if all(checks.values()) else "FAILED",
            "version": "clean-checkout-v1.2",
            "workflow": workflow,
        }


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument("--repository", required=True)
    command.add_argument("--commit", required=True)
    command.add_argument("--output", type=Path, required=True)
    command.add_argument("--workflow", choices=("demo", "full"), default="full")
    return command


def main() -> int:
    args = parser().parse_args()
    report = qualify(repository=args.repository, commit=args.commit, workflow=args.workflow)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(args.output)
    return 0 if report["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
