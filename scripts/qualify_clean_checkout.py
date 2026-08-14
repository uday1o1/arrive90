"""Verify the documented workflow from one exact remote commit in a fresh clone."""

from __future__ import annotations

import argparse
import json
import os
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
    ("install_python", ("uv", "python", "install")),
    ("sync_python", ("uv", "sync", "--frozen")),
    ("install_node", ("npm", "ci")),
    ("install_chromium", ("npx", "playwright", "install", "chromium")),
    ("local_and_browser_checks", ("make", "check-all")),
    ("security_scan", ("make", "security-scan")),
    (
        "security_evidence",
        (
            "uv",
            "run",
            "--no-sync",
            "python",
            "scripts/qualify_milestone_9_security.py",
            "--repository-report",
            "artifacts/runtime/security/repository.json",
            "--image-report",
            "artifacts/runtime/security/image.json",
            "--version-report",
            "artifacts/runtime/security/trivy-version.json",
            "--output",
            "artifacts/runtime/clean-checkout-security.json",
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
        "reliability_qualification",
        (
            "uv",
            "run",
            "--no-sync",
            "python",
            "scripts/qualify_milestone_9_reliability.py",
            "--output",
            "artifacts/runtime/clean-checkout-reliability.json",
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
)

DEMO_COMMANDS = (
    ("sync_python", ("uv", "sync", "--frozen")),
    ("network_free_demo", ("make", "demo")),
)


def _run(
    command: tuple[str, ...], *, cwd: Path, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # noqa: S603 - fixed repository-owned allow-list
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


def qualify(*, repository: str, commit: str, workflow: str = "full") -> dict[str, Any]:
    # Docker Desktop and Colima share the project filesystem, but do not necessarily
    # share the operating system's default temporary directory with the daemon.
    with tempfile.TemporaryDirectory(
        prefix="arrive90-clean-checkout-", dir=ROOT.parent
    ) as temporary:
        clone = Path(temporary) / "repository"
        clone_process = subprocess.run(  # noqa: S603 - fixed git clone operation
            [GIT, "clone", "--no-checkout", repository, str(clone)],
            check=False,
            capture_output=True,
            text=True,
        )
        results: list[dict[str, Any]] = [
            {
                "command": "git clone --no-checkout <repository>",
                "name": "clone",
                "status": clone_process.returncode,
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
        checkout = subprocess.run(  # noqa: S603 - fixed git checkout operation in temporary clone
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
                "status": checkout.returncode,
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
                "status": completed.returncode,
            }
            if completed.returncode != 0:
                result["stderr_tail"] = completed.stderr[-2_000:]
            results.append(result)
            if completed.returncode != 0:
                failing_command = name
                break
            if name == "local_and_browser_checks":
                passed_counts = [
                    int(value) for value in re.findall(r"(\d+) passed", completed.stdout)
                ]
                coverage = re.search(r"Total coverage: ([0-9.]+)%", completed.stdout)
                observations["browser_tests_passed"] = passed_counts[-1] if passed_counts else None
                observations["python_coverage_percent"] = (
                    float(coverage.group(1)) if coverage else None
                )
                observations["python_tests_passed"] = passed_counts[0] if passed_counts else None
            if name == "network_free_demo":
                manifest_path = clone / "artifacts/runtime/demo-terminal-manifest.json"
                expected_path = clone / "artifacts/demo/travel-time-v1/terminal-manifest.json"
                observations["terminal_manifest_reproduced"] = (
                    manifest_path.is_file()
                    and expected_path.is_file()
                    and manifest_path.read_bytes() == expected_path.read_bytes()
                )
        status = _run((GIT, "status", "--porcelain"), cwd=clone, env=env)
        head = _version((GIT, "rev-parse", "HEAD"), cwd=clone, env=env)
        for name in ("security", "licenses", "reliability", "repository-audit"):
            report_path = clone / "artifacts" / "runtime" / f"clean-checkout-{name}.json"
            if report_path.is_file():
                observations[f"{name}_status"] = json.loads(
                    report_path.read_text(encoding="utf-8")
                ).get("status")
        checks = {
            "all_documented_commands_passed": failing_command is None,
            "exact_commit_checked_out": head == commit,
            "fresh_clone_remained_clean": status.returncode == 0 and not status.stdout.strip(),
        }
        if workflow == "demo":
            checks["expected_terminal_manifest_reproduced"] = bool(
                observations.get("terminal_manifest_reproduced")
            )
        return {
            "checks": checks,
            "commit": commit,
            "environment": {
                "docker": _version(
                    ("docker", "version", "--format", "{{.Server.Version}}/{{.Server.Arch}}"),
                    cwd=clone,
                    env=env,
                ),
                "node": _version(("node", "--version"), cwd=clone, env=env),
                "npm": _version(("npm", "--version"), cwd=clone, env=env),
                "python": _version(
                    (str(clone / ".venv" / "bin" / "python"), "--version"), cwd=clone, env=env
                ),
                "uv": _version(("uv", "--version"), cwd=clone, env=env),
            },
            "failing_command": failing_command,
            "observations": observations,
            "repository": repository,
            "results": results,
            "status": "PASSED" if all(checks.values()) else "FAILED",
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
