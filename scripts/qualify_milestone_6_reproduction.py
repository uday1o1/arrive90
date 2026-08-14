"""Qualify a clean-clone full-year rebuild and verified incremental no-op rerun."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any


class QualificationError(ValueError):
    """The clean full-year reproduction could not be qualified."""


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise QualificationError(f"expected a JSON object: {path}")
    return payload


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _run(command: list[str], *, cwd: Path, environment: dict[str, str]) -> None:
    print(f"running: {' '.join(command)}", flush=True)
    completed = subprocess.run(  # noqa: S603 - repository and executable are explicit inputs
        command,
        cwd=cwd,
        check=False,
        env=environment,
    )
    if completed.returncode != 0:
        raise QualificationError(f"qualification command exited with status {completed.returncode}")


def _snapshot(root: Path) -> dict[str, tuple[int, int]]:
    return {
        path.relative_to(root).as_posix(): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _git_output(command: list[str], *, cwd: Path) -> str:
    completed = subprocess.run(  # noqa: S603 - fixed Git operation
        command,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def qualify(args: argparse.Namespace) -> dict[str, Any]:
    repository = args.repository.resolve()
    data_root = args.data_root.resolve()
    frozen_runtime = args.frozen_evaluation_runtime.resolve()
    rebuild_root = args.rebuild_root.resolve()
    if rebuild_root.exists():
        raise QualificationError(
            "the rebuild root must not exist so the first pass proves a complete rebuild"
        )
    if not (data_root / "raw").is_dir():
        raise QualificationError("the acquired immutable raw root is unavailable")
    if not frozen_runtime.is_dir():
        raise QualificationError("the frozen evaluation runtime is unavailable")
    output_parent = args.output.resolve().parent
    output_parent.mkdir(parents=True, exist_ok=True)
    first_terminal = output_parent / "milestone-6-reproduction-first-terminal.json"
    second_terminal = output_parent / "milestone-6-reproduction-second-terminal.json"
    first_execution = output_parent / "milestone-6-reproduction-first-execution.json"
    second_execution = output_parent / "milestone-6-reproduction-second-execution.json"

    with tempfile.TemporaryDirectory(prefix="arrive90-m6-clean-") as temporary:
        temporary_root = Path(temporary)
        clone = temporary_root / "checkout"
        environment = dict(os.environ)
        environment["UV_CACHE_DIR"] = str(temporary_root / "uv-cache")
        _run(
            ["git", "clone", "--no-local", str(repository), str(clone)],
            cwd=temporary_root,
            environment=environment,
        )
        _run(
            ["git", "checkout", "--detach", args.commit],
            cwd=clone,
            environment=environment,
        )
        observed_commit = _git_output(["git", "rev-parse", "HEAD"], cwd=clone)
        if observed_commit != args.commit:
            raise QualificationError("fresh checkout did not resolve to the requested commit")
        _run(
            ["uv", "sync", "--frozen", "--all-groups"],
            cwd=clone,
            environment=environment,
        )
        expected = clone / "artifacts/reproduction/full-year-terminal.json"

        def reproduce(output: Path, execution: Path) -> None:
            _run(
                [
                    "uv",
                    "run",
                    "--frozen",
                    "--no-sync",
                    "python",
                    "scripts/reproduce_full_year.py",
                    "--data-root",
                    str(data_root),
                    "--rebuild-root",
                    str(rebuild_root),
                    "--frozen-evaluation-runtime",
                    str(frozen_runtime),
                    "--expected",
                    str(expected),
                    "--output",
                    str(output),
                    "--execution-report",
                    str(execution),
                ],
                cwd=clone,
                environment=environment,
            )

        reproduce(first_terminal, first_execution)
        first_snapshot = _snapshot(rebuild_root)
        reproduce(second_terminal, second_execution)
        second_snapshot = _snapshot(rebuild_root)
        clean_status = _git_output(["git", "status", "--porcelain"], cwd=clone)

    first = _load(first_execution)
    second = _load(second_execution)
    first_actions = first.get("actions", {})
    second_actions = second.get("actions", {})
    rebuild_stages = {
        "episode_and_dataset_generation",
        "frozen_evaluation_rebuild",
        "normalization",
        "training",
    }
    expected_hash = _digest(repository / "artifacts/reproduction/full-year-terminal.json")
    checks = {
        "clean_checkout_remained_clean": clean_status == "",
        "first_pass_rebuilt_every_derived_stage": (
            first_actions.get("acquisition") == "VERIFIED_IMMUTABLE_LOCK"
            and all(first_actions.get(stage) == "REBUILT" for stage in rebuild_stages)
        ),
        "first_terminal_matches_committed_expectation": (_digest(first_terminal) == expected_hash),
        "no_op_rerun_did_not_rewrite_derived_outputs": first_snapshot == second_snapshot,
        "no_op_rerun_verified_every_derived_stage": (
            second_actions.get("acquisition") == "VERIFIED_IMMUTABLE_LOCK"
            and all(second_actions.get(stage) == "VERIFIED_NOOP" for stage in rebuild_stages)
        ),
        "second_terminal_matches_first_and_expectation": (
            _digest(second_terminal) == _digest(first_terminal) == expected_hash
        ),
    }
    return {
        "acceptance_version": "travel-time-v1.2",
        "checks": checks,
        "commit": args.commit,
        "failing_checks": sorted(key for key, passed in checks.items() if not passed),
        "first_actions": first_actions,
        "immutable_output_file_count": len(first_snapshot),
        "rebuild_root_kind": "ignored external runtime",
        "second_actions": second_actions,
        "status": "PASSED" if all(checks.values()) else "FAILED",
        "terminal_manifest_sha256": expected_hash,
        "version": "milestone-6-clean-reproduction-v1",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--frozen-evaluation-runtime", type=Path, required=True)
    parser.add_argument("--rebuild-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    report = qualify(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(args.output)
    return 0 if report["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
