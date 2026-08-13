"""Audit whether public historical sources satisfy the Milestone 0 evidence contract."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shlex
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq  # type: ignore[import-untyped]
import yaml


@dataclass(frozen=True)
class AuditInputs:
    """Immutable paths and identifiers used by one source audit."""

    index: Path
    parquet: Path
    data_dictionary: Path
    transformation_source: Path
    license_pdf: Path
    acceptance_charter: Path
    source_commit: str
    command: str


def sha256_file(path: Path) -> str:
    """Return a lowercase SHA-256 digest for one immutable input."""

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_index(path: Path) -> dict[str, Any]:
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    required = {"size_bytes", "last_modified", "service_date", "file_url"}
    fieldnames = set(rows[0]) if rows else set()
    missing_fields = sorted(required - fieldnames)
    parsed_dates = [date.fromisoformat(row["service_date"]) for row in rows]
    duplicate_dates = len(parsed_dates) - len(set(parsed_dates))
    return {
        "row_count": len(rows),
        "first_service_date": min(parsed_dates).isoformat() if parsed_dates else None,
        "last_service_date": max(parsed_dates).isoformat() if parsed_dates else None,
        "missing_fields": missing_fields,
        "duplicate_service_dates": duplicate_dates,
    }


def _schema_fingerprint(path: Path) -> tuple[list[str], str]:
    schema = pq.read_schema(path)
    names = list(schema.names)
    serialized = schema.serialize().to_pybytes()
    return names, hashlib.sha256(serialized).hexdigest()


def _charter_hash(path: Path) -> tuple[str, dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        loaded = yaml.safe_load(stream)
    if not isinstance(loaded, dict):
        raise ValueError("acceptance charter must be a mapping")
    return sha256_file(path), loaded


def run_source_audit(inputs: AuditInputs) -> dict[str, Any]:
    """Build a deterministic Milestone 0 source-feasibility report."""

    index_summary = _read_index(inputs.index)
    schema_names, schema_fingerprint = _schema_fingerprint(inputs.parquet)
    dictionary_text = inputs.data_dictionary.read_text(encoding="utf-8")
    transform_text = inputs.transformation_source.read_text(encoding="utf-8")
    charter_hash, charter = _charter_hash(inputs.acceptance_charter)

    daily_stop_is_documented_coalesced = (
        ("Trip Update" in dictionary_text or "StopTimeUpdate" in dictionary_text)
        and "VehiclePosition" in dictionary_text
        and "stop_timestamp" in dictionary_text
    )
    transform_coalesces_stop_sources = all(
        token in transform_text
        for token in (
            "sa.func.coalesce",
            "VehicleEvents.vp_stop_timestamp",
            "VehicleEvents.tu_stop_timestamp",
            '.label("stop_timestamp")',
        )
    )
    explicit_vp_stop_provenance = "vp_stop_timestamp" in schema_names
    explicit_evidence_discriminator = any(
        name in schema_names for name in ("arrival_evidence", "stop_timestamp_source")
    )
    move_evidence_available = "move_timestamp" in schema_names
    direct_boarding_evidence_identifiable = (
        explicit_vp_stop_provenance or explicit_evidence_discriminator
    )

    checks = {
        "source_index_contract_valid": (
            index_summary["row_count"] > 0
            and not index_summary["missing_fields"]
            and index_summary["duplicate_service_dates"] == 0
        ),
        "public_parquet_schema_inspected": bool(schema_names),
        "daily_stop_timestamp_coalescence_documented": daily_stop_is_documented_coalesced,
        "transformation_coalescence_verified": transform_coalesces_stop_sources,
        "vehicle_position_move_evidence_available": move_evidence_available,
        "direct_vehicle_position_stop_provenance_available": explicit_vp_stop_provenance,
        "boarding_evidence_discriminator_available": explicit_evidence_discriminator,
        "primary_boarding_evidence_identifiable": direct_boarding_evidence_identifiable,
        "supported_scope_frozen": bool(charter.get("scope", {}).get("scope_frozen")),
        "primary_outcome_semantic_frozen": bool(
            charter.get("primary_outcome", {}).get("selected_semantic")
        ),
    }
    required_passing_checks = (
        "source_index_contract_valid",
        "public_parquet_schema_inspected",
        "daily_stop_timestamp_coalescence_documented",
        "transformation_coalescence_verified",
        "vehicle_position_move_evidence_available",
        "direct_vehicle_position_stop_provenance_available",
        "primary_boarding_evidence_identifiable",
        "supported_scope_frozen",
        "primary_outcome_semantic_frozen",
    )
    failing_checks = [name for name in required_passing_checks if not checks[name]]
    status = "PASSED" if not failing_checks else "FAILED"

    input_paths = {
        "lamp_index": inputs.index,
        "representative_parquet": inputs.parquet,
        "lamp_data_dictionary": inputs.data_dictionary,
        "lamp_transformation_source": inputs.transformation_source,
        "massdot_license": inputs.license_pdf,
        "acceptance_charter": inputs.acceptance_charter,
    }
    return {
        "milestone": 0,
        "status": status,
        "acceptance_version": charter.get("acceptance_version"),
        "acceptance_version_hash": charter_hash,
        "command": inputs.command,
        "source_commit": inputs.source_commit,
        "input_manifest_hashes": {name: sha256_file(path) for name, path in input_paths.items()},
        "index_summary": index_summary,
        "representative_parquet": {
            "schema_fields": schema_names,
            "schema_fingerprint": schema_fingerprint,
        },
        "checks": checks,
        "failing_checks": failing_checks,
        "blocking_prerequisite": (
            None
            if status == "PASSED"
            else "Historical rail Vehicle Position STOPPED_AT primitives must be separately "
            "identifiable from Trip Update predictions with defensible availability lineage."
        ),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--parquet", type=Path, required=True)
    parser.add_argument("--data-dictionary", type=Path, required=True)
    parser.add_argument("--transformation-source", type=Path, required=True)
    parser.add_argument("--license", dest="license_pdf", type=Path, required=True)
    parser.add_argument("--acceptance-charter", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--reported-command")
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> None:
    """Run the audit CLI and write stable, sorted JSON."""

    args = _parser().parse_args()
    command = args.reported_command or shlex.join(sys.argv)
    inputs = AuditInputs(
        index=args.index,
        parquet=args.parquet,
        data_dictionary=args.data_dictionary,
        transformation_source=args.transformation_source,
        license_pdf=args.license_pdf,
        acceptance_charter=args.acceptance_charter,
        source_commit=args.source_commit,
        command=command,
    )
    report = run_source_audit(inputs)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "failing_checks": report["failing_checks"]}))


if __name__ == "__main__":
    main()
