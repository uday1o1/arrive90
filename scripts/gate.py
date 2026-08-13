"""Fail closed unless a milestone gate report exists and is exactly PASSED."""

from __future__ import annotations

import sys
from pathlib import Path

from arrive90_data_contracts.gates import load_report, validate_gate_report


def main() -> int:
    """Return zero only for an existing report with status PASSED."""

    if len(sys.argv) != 2 or not sys.argv[1].isdigit():
        print("usage: gate.py MILESTONE", file=sys.stderr)
        return 2
    milestone = int(sys.argv[1])
    path = Path("artifacts/reports/gates") / f"milestone-{milestone}.json"
    if not path.is_file():
        print(f"milestone {milestone} gate report is missing: {path}", file=sys.stderr)
        return 1
    report = load_report(path)
    errors = validate_gate_report(report, milestone)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        for check in report.get("failing_checks", []):
            print(f"- {check}", file=sys.stderr)
        return 1
    print(f"milestone {milestone} gate passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
