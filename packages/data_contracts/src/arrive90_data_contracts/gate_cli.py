"""User-facing milestone gate runner shared by both CLI entry points."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from arrive90_data_contracts.gates import (
    DEFAULT_ACCEPTANCE_VERSION,
    load_report,
    validate_gate_report,
)


def run_gate(
    milestone: int,
    *,
    report_root: Path = Path("artifacts/reports/gates"),
    acceptance_version: str = DEFAULT_ACCEPTANCE_VERSION,
) -> int:
    """Return zero only for an existing, matching ACCEPTED report."""

    path = report_root / f"milestone-{milestone}.json"
    if not path.is_file():
        print(f"milestone {milestone} gate report is missing: {path}", file=sys.stderr)
        return 1
    try:
        report = load_report(path)
    except (OSError, ValueError) as load_error:
        print(f"milestone {milestone} gate report is invalid: {load_error}", file=sys.stderr)
        return 1
    errors = validate_gate_report(report, milestone, acceptance_version)
    if errors:
        for validation_error in errors:
            print(validation_error, file=sys.stderr)
        for check in report.get("failing_checks", []):
            print(f"- {check}", file=sys.stderr)
        return 1
    print(f"milestone {milestone} gate ACCEPTED")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Parse the standalone gate command."""

    parser = argparse.ArgumentParser()
    parser.add_argument("milestone", type=int)
    parser.add_argument("--report-root", type=Path, default=Path("artifacts/reports/gates"))
    parser.add_argument("--acceptance-version", default=DEFAULT_ACCEPTANCE_VERSION)
    args = parser.parse_args(argv)
    if args.milestone < 0:
        parser.error("milestone must be nonnegative")
    return run_gate(
        args.milestone,
        report_root=args.report_root,
        acceptance_version=args.acceptance_version,
    )
