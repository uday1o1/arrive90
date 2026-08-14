from __future__ import annotations

import json
from pathlib import Path

import pytest
from arrive90_data_contracts.gate_cli import run_gate
from arrive90_data_contracts.gates import GateState, load_report, validate_gate_report

_BASE_REPORT = {
    "milestone": 2,
    "acceptance_version": "travel-time-v1.2",
    "state": "ACCEPTED",
}


def test_load_report_requires_valid_json_object(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    report.write_text("[]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object"):
        load_report(report)
    report.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="valid JSON"):
        load_report(report)


@pytest.mark.parametrize("state", list(GateState))
def test_validate_gate_report_recognizes_every_current_state(state: GateState) -> None:
    report = {**_BASE_REPORT, "state": state.value}
    errors = validate_gate_report(report, 2)
    if state is GateState.ACCEPTED:
        assert errors == []
    else:
        assert errors == [f"milestone 2 gate is not accepted: {state.value}"]


def test_validate_gate_report_rejects_mismatches_unknown_and_legacy_reports() -> None:
    assert validate_gate_report({**_BASE_REPORT, "milestone": 1}, 2) == [
        "gate report milestone mismatch: expected 2"
    ]
    assert validate_gate_report({**_BASE_REPORT, "acceptance_version": "v1"}, 2) == [
        "gate report acceptance version mismatch: expected travel-time-v1.2"
    ]
    assert validate_gate_report({**_BASE_REPORT, "state": "UNKNOWN"}, 2) == [
        "invalid gate state: 'UNKNOWN'"
    ]
    assert validate_gate_report(
        {"milestone": 2, "acceptance_version": "travel-time-v1.2", "status": "PASSED"},
        2,
    ) == [
        "legacy gate report key is forbidden: status",
        "invalid gate state: None",
    ]


@pytest.mark.parametrize(
    "state",
    [GateState.NOT_STARTED, GateState.IN_PROGRESS, GateState.BLOCKED, GateState.FAILED],
)
def test_gate_runner_exits_nonzero_for_every_nonaccepted_state(
    state: GateState, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report_root = tmp_path / "gates"
    report_root.mkdir()
    path = report_root / "milestone-0.json"
    path.write_text(
        json.dumps({**_BASE_REPORT, "milestone": 0, "state": state.value}),
        encoding="utf-8",
    )
    assert run_gate(0, report_root=report_root) == 1
    assert "not accepted" in capsys.readouterr().err


def test_gate_runner_accepts_only_current_accepted_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report_root = tmp_path / "gates"
    report_root.mkdir()
    path = report_root / "milestone-0.json"
    path.write_text(json.dumps({**_BASE_REPORT, "milestone": 0}), encoding="utf-8")
    assert run_gate(0, report_root=report_root) == 0
    assert "ACCEPTED" in capsys.readouterr().out


@pytest.mark.parametrize(
    "report",
    [
        {"milestone": 0, "acceptance_version": "travel-time-v1.2"},
        {**_BASE_REPORT, "milestone": 0, "state": "UNKNOWN"},
        {"milestone": 0, "acceptance_version": "travel-time-v1.2", "status": "PASSED"},
        {**_BASE_REPORT, "milestone": 1},
        {**_BASE_REPORT, "milestone": 0, "acceptance_version": "legacy"},
    ],
)
def test_gate_runner_rejects_every_invalid_report_shape(
    report: dict[str, object],
    tmp_path: Path,
) -> None:
    report_root = tmp_path / "gates"
    report_root.mkdir()
    (report_root / "milestone-0.json").write_text(json.dumps(report), encoding="utf-8")
    assert run_gate(0, report_root=report_root) == 1


def test_gate_runner_rejects_malformed_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report_root = tmp_path / "gates"
    report_root.mkdir()
    path = report_root / "milestone-0.json"

    path.write_text("{", encoding="utf-8")
    assert run_gate(0, report_root=report_root) == 1
    assert "invalid" in capsys.readouterr().err


def test_gate_runner_fails_when_report_is_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_gate(7, report_root=tmp_path) == 1
    assert "missing" in capsys.readouterr().err
