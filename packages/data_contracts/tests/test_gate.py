from __future__ import annotations

import json
from pathlib import Path

import pytest
from arrive90_data_contracts.gates import load_report, validate_gate_report


def test_load_report_requires_object(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    report.write_text("[]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object"):
        load_report(report)


def test_load_report_preserves_valid_status(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"milestone": 0, "status": "FAILED"}), encoding="utf-8")
    assert load_report(report)["status"] == "FAILED"


def test_validate_gate_report_accepts_only_matching_pass() -> None:
    assert validate_gate_report({"milestone": 2, "status": "PASSED"}, 2) == []
    assert validate_gate_report({"milestone": 1, "status": "PASSED"}, 2) == [
        "gate report milestone mismatch: expected 2"
    ]
    assert validate_gate_report({"milestone": 2, "status": "FAILED"}, 2) == [
        "milestone 2 gate did not pass: FAILED"
    ]
    assert validate_gate_report({"milestone": 2, "status": "UNKNOWN"}, 2) == [
        "invalid gate status: 'UNKNOWN'"
    ]
