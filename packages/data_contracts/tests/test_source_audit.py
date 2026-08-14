from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path

import arrive90_data_contracts
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest
from arrive90_data_contracts.source_audit import (
    AuditInputs,
    main,
    run_source_audit,
    sha256_file,
)


def _write_inputs(tmp_path: Path, *, expose_vp_stop: bool) -> AuditInputs:
    index = tmp_path / "index.csv"
    with index.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=("size_bytes", "last_modified", "service_date", "file_url"),
        )
        writer.writeheader()
        writer.writerow(
            {
                "size_bytes": "123",
                "last_modified": "2026-01-01 00:00:00+00:00",
                "service_date": "2025-01-01",
                "file_url": "https://example.invalid/fixture.parquet",
            }
        )

    fields: dict[str, pa.Array] = {
        "move_timestamp": pa.array([1], type=pa.int64()),
        "stop_timestamp": pa.array([2], type=pa.int64()),
    }
    if expose_vp_stop:
        fields["vp_stop_timestamp"] = pa.array([2], type=pa.int64())
    parquet = tmp_path / "fixture.parquet"
    pq.write_table(pa.table(fields), parquet)

    dictionary = tmp_path / "Data_Dictionary.md"
    dictionary.write_text(
        "stop_timestamp uses VehiclePosition STOPPED_AT or Trip Update when unavailable.\n",
        encoding="utf-8",
    )
    transform = tmp_path / "flat_file.py"
    transform.write_text(
        "sa.func.coalesce(VehicleEvents.vp_stop_timestamp, "
        'VehicleEvents.tu_stop_timestamp).label("stop_timestamp")\n',
        encoding="utf-8",
    )
    license_pdf = tmp_path / "license.pdf"
    license_pdf.write_bytes(b"synthetic-license-fixture")
    charter = tmp_path / "v1.yaml"
    charter.write_text(
        "acceptance_version: v1\n"
        "scope:\n"
        f"  scope_frozen: {'true' if expose_vp_stop else 'false'}\n"
        "primary_outcome:\n"
        f"  selected_semantic: {'VP_STOP_OBSERVATION_INTERVAL' if expose_vp_stop else 'null'}\n",
        encoding="utf-8",
    )
    return AuditInputs(
        index=index,
        parquet=parquet,
        data_dictionary=dictionary,
        transformation_source=transform,
        license_pdf=license_pdf,
        acceptance_charter=charter,
        source_commit="fixture-commit",
        command="fixture-command",
    )


def test_sha256_file_reads_binary_content(tmp_path: Path) -> None:
    fixture = tmp_path / "input.bin"
    fixture.write_bytes(b"arrive90")
    assert sha256_file(fixture) == hashlib.sha256(b"arrive90").hexdigest()


def test_coalesced_public_schema_fails_primary_boarding_gate(tmp_path: Path) -> None:
    report = run_source_audit(_write_inputs(tmp_path, expose_vp_stop=False))
    assert report["status"] == "FAILED"
    assert report["checks"]["transformation_coalescence_verified"] is True
    assert report["checks"]["direct_vehicle_position_stop_provenance_available"] is False
    assert report["checks"]["primary_boarding_evidence_identifiable"] is False
    assert "primary_boarding_evidence_identifiable" in report["failing_checks"]


def test_explicit_vehicle_position_schema_can_pass_source_shape_gate(tmp_path: Path) -> None:
    report = run_source_audit(_write_inputs(tmp_path, expose_vp_stop=True))
    assert report["status"] == "PASSED"
    assert report["failing_checks"] == []


def test_index_duplicate_service_dates_fail_contract(tmp_path: Path) -> None:
    inputs = _write_inputs(tmp_path, expose_vp_stop=True)
    with inputs.index.open("a", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(("456", "2026-01-02 00:00:00+00:00", "2025-01-01", "https://invalid/2"))
    report = run_source_audit(inputs)
    assert report["status"] == "FAILED"
    assert report["index_summary"]["duplicate_service_dates"] == 1
    assert report["checks"]["source_index_contract_valid"] is False


def test_non_mapping_acceptance_charter_is_rejected(tmp_path: Path) -> None:
    inputs = _write_inputs(tmp_path, expose_vp_stop=True)
    inputs.acceptance_charter.write_text("- invalid\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a mapping"):
        run_source_audit(inputs)


def test_cli_writes_sorted_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    inputs = _write_inputs(tmp_path, expose_vp_stop=False)
    output = tmp_path / "reports" / "milestone-0.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "arrive90-source-audit",
            "--index",
            str(inputs.index),
            "--parquet",
            str(inputs.parquet),
            "--data-dictionary",
            str(inputs.data_dictionary),
            "--transformation-source",
            str(inputs.transformation_source),
            "--license",
            str(inputs.license_pdf),
            "--acceptance-charter",
            str(inputs.acceptance_charter),
            "--source-commit",
            inputs.source_commit,
            "--reported-command",
            "fixture audit",
            "--output",
            str(output),
        ],
    )
    main()
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["command"] == "fixture audit"
    assert report["status"] == "FAILED"
    assert '"status": "FAILED"' in capsys.readouterr().out


def test_source_audit_public_exports_are_lazy_and_compatible() -> None:
    assert arrive90_data_contracts.AuditInputs is AuditInputs
    assert arrive90_data_contracts.run_source_audit is run_source_audit

    def read_unknown() -> object:
        return arrive90_data_contracts.unknown

    with pytest.raises(AttributeError):
        read_unknown()
