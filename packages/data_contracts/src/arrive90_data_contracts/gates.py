"""Fail-closed milestone gate report validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

VALID_STATUSES = frozenset({"PASSED", "INSUFFICIENT_EVIDENCE", "FAILED"})


def load_report(path: Path) -> dict[str, Any]:
    """Load one gate report and validate its top-level shape."""

    with path.open(encoding="utf-8") as stream:
        report = json.load(stream)
    if not isinstance(report, dict):
        raise ValueError("gate report must be a JSON object")
    return report


def validate_gate_report(report: dict[str, Any], milestone: int) -> list[str]:
    """Return every reason a report cannot unlock its milestone."""

    errors: list[str] = []
    if report.get("milestone") != milestone:
        errors.append(f"gate report milestone mismatch: expected {milestone}")
    status = report.get("status")
    if status not in VALID_STATUSES:
        errors.append(f"invalid gate status: {status!r}")
    elif status != "PASSED":
        errors.append(f"milestone {milestone} gate did not pass: {status}")
    return errors
