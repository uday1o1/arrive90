"""Fail-closed travel-time-v1 milestone gate report validation."""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Any


class GateState(StrEnum):
    """The only milestone states accepted by the current charter."""

    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    ACCEPTED = "ACCEPTED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


VALID_STATES = frozenset(state.value for state in GateState)
DEFAULT_ACCEPTANCE_VERSION = "travel-time-v1"


def load_report(path: Path) -> dict[str, Any]:
    """Load one gate report and validate its top-level shape."""

    try:
        with path.open(encoding="utf-8") as stream:
            report = json.load(stream)
    except json.JSONDecodeError as error:
        raise ValueError("gate report must be valid JSON") from error
    if not isinstance(report, dict):
        raise ValueError("gate report must be a JSON object")
    return report


def validate_gate_report(
    report: dict[str, Any],
    milestone: int,
    acceptance_version: str = DEFAULT_ACCEPTANCE_VERSION,
) -> list[str]:
    """Return every reason a report cannot unlock its milestone."""

    errors: list[str] = []
    if report.get("milestone") != milestone:
        errors.append(f"gate report milestone mismatch: expected {milestone}")
    if report.get("acceptance_version") != acceptance_version:
        errors.append(f"gate report acceptance version mismatch: expected {acceptance_version}")
    if "status" in report:
        errors.append("legacy gate report key is forbidden: status")
    state = report.get("state")
    if state not in VALID_STATES:
        errors.append(f"invalid gate state: {state!r}")
    elif state != GateState.ACCEPTED:
        errors.append(f"milestone {milestone} gate is not accepted: {state}")
    return errors
