"""Unified Arrive90 command-line entry point."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from arrive90_data_contracts.gate_cli import run_gate
from arrive90_data_contracts.gates import DEFAULT_ACCEPTANCE_VERSION

from arrive90_ingestion.inventory import (
    INVENTORY_URL,
    InventoryError,
    download_inventory,
    write_inventory_lock,
)
from arrive90_ingestion.pinned_sources import (
    DEFAULT_ACQUISITION_LOCK,
    DEFAULT_BUS_PROFILE,
    DEFAULT_INVENTORY_LOCK,
    DEFAULT_RAW_ROOT,
    DEFAULT_SCHEDULE_PROFILE,
    acquire_pinned_day,
    result_payload,
)
from arrive90_ingestion.year_acquisition import (
    DEFAULT_FULL_ACQUISITION_LOCK,
    acquire_full_year,
)
from arrive90_ingestion.year_normalization import (
    DEFAULT_NORMALIZED_ROOT,
    normalize_year,
)
from arrive90_ingestion.year_normalization import (
    DEFAULT_RUNTIME_ROOT as DEFAULT_NORMALIZATION_RUNTIME_ROOT,
)


def _source_lock(args: argparse.Namespace) -> int:
    body = args.inventory_file.read_bytes() if args.inventory_file else download_inventory(args.url)
    snapshot_path, lock_sha256 = write_inventory_lock(
        body,
        snapshot_directory=args.snapshot_directory,
        lock_path=args.output,
        inventory_url=args.url,
    )
    print(
        json.dumps(
            {
                "lock_path": str(args.output),
                "lock_sha256": lock_sha256,
                "snapshot_path": str(snapshot_path),
            },
            sort_keys=True,
        )
    )
    return 0


def _gate(args: argparse.Namespace) -> int:
    return run_gate(
        args.milestone,
        report_root=args.report_root,
        acceptance_version=args.acceptance_version,
    )


def _source_download(args: argparse.Namespace) -> int:
    if args.year is not None:
        if args.include_schedule:
            raise ValueError("--include-schedule is implicit and cannot be combined with --year")
        full_result = acquire_full_year(
            args.year,
            inventory_lock_path=args.inventory_lock,
            pinned_acquisition_lock_path=args.acquisition_lock,
            raw_root=args.raw_root,
            acquisition_lock_path=args.full_acquisition_lock,
            workers=args.workers,
        )
        print(
            json.dumps(
                {
                    "acquisition_lock_path": str(full_result.acquisition_lock_path),
                    "acquisition_lock_sha256": full_result.acquisition_lock_sha256,
                    "object_count": full_result.object_count,
                    "schedule_database_sha256": full_result.schedule_database_sha256,
                    "schema_fingerprints": full_result.schema_fingerprints,
                    "total_row_count": full_result.total_row_count,
                    "total_size_bytes": full_result.total_size_bytes,
                },
                sort_keys=True,
            )
        )
        return 0
    if args.date is None:
        raise ValueError("one of --date or --year is required")
    pinned_result = acquire_pinned_day(
        args.date,
        include_schedule=args.include_schedule,
        inventory_lock_path=args.inventory_lock,
        bus_profile_path=args.bus_profile,
        schedule_profile_path=args.schedule_profile,
        raw_root=args.raw_root,
        acquisition_lock_path=args.acquisition_lock,
    )
    print(json.dumps(result_payload(pinned_result), sort_keys=True))
    return 0


def _data_qualify_day(args: argparse.Namespace) -> int:
    from arrive90_evaluation.travel_time_qualification import qualify_day

    result = qualify_day(
        args.date,
        raw_root=args.raw_root,
        bus_profile_path=args.bus_profile,
        schedule_profile_path=args.schedule_profile,
        acquisition_lock_path=args.acquisition_lock,
        acceptance_charter_path=args.acceptance_charter,
        runtime_root=args.runtime_root,
    )
    print(
        json.dumps(
            {
                "checks_passed": result.checks_passed,
                "example_manifest_path": str(result.example_manifest_path),
                "example_manifest_sha256": result.example_manifest_sha256,
                "normalized_manifest_path": str(result.normalized_manifest_path),
                "normalized_manifest_sha256": result.normalized_manifest_sha256,
                "run_summary_path": str(result.run_summary_path),
                "run_summary_sha256": result.run_summary_sha256,
            },
            sort_keys=True,
        )
    )
    return 0 if result.checks_passed else 1


def _data_normalize(args: argparse.Namespace) -> int:
    result = normalize_year(
        args.year,
        inventory_lock_path=args.inventory_lock,
        acquisition_lock_path=args.acquisition_lock,
        raw_root=args.raw_root,
        normalized_root=args.normalized_root,
        runtime_root=args.runtime_root,
    )
    print(
        json.dumps(
            {
                "dataset_manifest_path": str(result.dataset_manifest_path),
                "dataset_manifest_sha256": result.dataset_manifest_sha256,
                "observation_count": result.observation_count,
                "partition_count": result.partition_count,
                "quarantine_count": result.quarantine_count,
                "runtime_report_path": str(result.runtime_report_path),
                "schedule_index_path": str(result.schedule_index_path),
                "schedule_index_sha256": result.schedule_index_sha256,
            },
            sort_keys=True,
        )
    )
    return 0


def _data_build_dataset(args: argparse.Namespace) -> int:
    from arrive90_evaluation.model_population import (
        build_model_population,
        run_dataset_contract_probe,
    )
    from arrive90_evaluation.year_dataset import build_unsampled_audit

    if args.qualification_probe is not None:
        probe = run_dataset_contract_probe(args.qualification_probe)
        if args.qualification_probe_only:
            print(json.dumps(probe, sort_keys=True))
            return 0
    elif args.qualification_probe_only:
        raise ValueError("--qualification-probe-only requires --qualification-probe")

    unsampled = None
    if not args.population_only:
        unsampled = build_unsampled_audit(
            normalized_root=args.normalized_root,
            dataset_root=args.dataset_root,
            schedule_database=args.schedule_database,
            runtime_root=args.runtime_root,
        )
    if args.unsampled_only:
        if unsampled is None:
            raise ValueError("--unsampled-only requires an unsampled build")
        print(
            json.dumps(
                {
                    "candidate_example_count": unsampled.candidate_example_count,
                    "episode_count": unsampled.episode_count,
                    "manifest_path": str(unsampled.manifest_path),
                    "manifest_sha256": unsampled.manifest_sha256,
                    "outcome_example_count": unsampled.outcome_example_count,
                    "runtime_report_path": str(unsampled.runtime_report_path),
                },
                sort_keys=True,
            )
        )
        return 0
    population = build_model_population(
        normalized_root=args.normalized_root,
        dataset_root=args.dataset_root,
        schedule_database=args.schedule_database,
        runtime_root=args.runtime_root,
    )
    print(
        json.dumps(
            {
                "benchmark_report_path": str(population.benchmark_report_path),
                "model_population_manifest_path": str(population.manifest_path),
                "model_population_manifest_sha256": population.manifest_sha256,
                "population_runtime_report_path": str(population.runtime_report_path),
                "selected_anchor_count": population.selected_anchor_count,
                "selected_example_count": population.selected_example_count,
                "unsampled_candidate_example_count": (
                    unsampled.candidate_example_count if unsampled is not None else None
                ),
                "unsampled_episode_count": unsampled.episode_count
                if unsampled is not None
                else None,
                "unsampled_manifest_path": (
                    str(unsampled.manifest_path) if unsampled is not None else None
                ),
                "unsampled_manifest_sha256": (
                    unsampled.manifest_sha256 if unsampled is not None else None
                ),
                "unsampled_outcome_example_count": (
                    unsampled.outcome_example_count if unsampled is not None else None
                ),
            },
            sort_keys=True,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the public CLI surface."""

    parser = argparse.ArgumentParser(prog="arrive90")
    commands = parser.add_subparsers(dest="command", required=True)

    source = commands.add_parser("source")
    source_commands = source.add_subparsers(dest="source_command", required=True)
    lock = source_commands.add_parser("lock")
    lock.add_argument("--url", default=INVENTORY_URL)
    lock.add_argument("--inventory-file", type=Path)
    lock.add_argument(
        "--snapshot-directory",
        type=Path,
        default=Path("configs/source-locks/inventory-snapshots"),
    )
    lock.add_argument(
        "--output",
        type=Path,
        default=Path("configs/source-locks/mbta-2024.json"),
    )
    lock.set_defaults(handler=_source_lock)

    download = source_commands.add_parser("download")
    download_scope = download.add_mutually_exclusive_group(required=True)
    download_scope.add_argument("--date", type=date.fromisoformat)
    download_scope.add_argument("--year", type=int)
    download.add_argument("--include-schedule", action="store_true")
    download.add_argument("--workers", type=int, default=4)
    download.add_argument("--inventory-lock", type=Path, default=DEFAULT_INVENTORY_LOCK)
    download.add_argument("--bus-profile", type=Path, default=DEFAULT_BUS_PROFILE)
    download.add_argument("--schedule-profile", type=Path, default=DEFAULT_SCHEDULE_PROFILE)
    download.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    download.add_argument("--acquisition-lock", type=Path, default=DEFAULT_ACQUISITION_LOCK)
    download.add_argument(
        "--full-acquisition-lock",
        type=Path,
        default=DEFAULT_FULL_ACQUISITION_LOCK,
    )
    download.set_defaults(handler=_source_download)

    data = commands.add_parser("data")
    data_commands = data.add_subparsers(dest="data_command", required=True)
    normalize = data_commands.add_parser("normalize")
    normalize.add_argument("--year", type=int, required=True)
    normalize.add_argument("--inventory-lock", type=Path, default=DEFAULT_INVENTORY_LOCK)
    normalize.add_argument("--acquisition-lock", type=Path, default=DEFAULT_FULL_ACQUISITION_LOCK)
    normalize.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    normalize.add_argument("--normalized-root", type=Path, default=DEFAULT_NORMALIZED_ROOT)
    normalize.add_argument("--runtime-root", type=Path, default=DEFAULT_NORMALIZATION_RUNTIME_ROOT)
    normalize.set_defaults(handler=_data_normalize)
    build_dataset = data_commands.add_parser("build-dataset")
    build_dataset.add_argument("--normalized-root", type=Path, default=DEFAULT_NORMALIZED_ROOT)
    build_dataset.add_argument(
        "--dataset-root", type=Path, default=Path("data/datasets/travel-time-v1")
    )
    build_dataset.add_argument(
        "--schedule-database",
        type=Path,
        default=Path("data/raw/mbta-gtfs/2024/GTFS_ARCHIVE.db"),
    )
    build_dataset.add_argument(
        "--runtime-root",
        type=Path,
        default=Path("artifacts/runtime/milestone-2"),
    )
    build_phase = build_dataset.add_mutually_exclusive_group()
    build_phase.add_argument(
        "--population-only",
        action="store_true",
        help="resume from the verified active unsampled manifest",
    )
    build_phase.add_argument(
        "--unsampled-only",
        action="store_true",
        help="build and verify only the unsampled audit phase",
    )
    build_dataset.add_argument(
        "--qualification-probe",
        choices=(
            "CONTROL",
            "FUTURE_OBSERVATION",
            "FINAL_EPISODE_LENGTH",
            "FUTURE_SCHEDULE",
            "POST_OUTCOME_AGGREGATE",
            "SPLIT_LEAKAGE",
        ),
        help=argparse.SUPPRESS,
    )
    build_dataset.add_argument(
        "--qualification-probe-only",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    build_dataset.set_defaults(handler=_data_build_dataset)
    qualify_day = data_commands.add_parser("qualify-day")
    qualify_day.add_argument("--date", type=date.fromisoformat, required=True)
    qualify_day.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    qualify_day.add_argument("--bus-profile", type=Path, default=DEFAULT_BUS_PROFILE)
    qualify_day.add_argument("--schedule-profile", type=Path, default=DEFAULT_SCHEDULE_PROFILE)
    qualify_day.add_argument("--acquisition-lock", type=Path, default=DEFAULT_ACQUISITION_LOCK)
    qualify_day.add_argument(
        "--acceptance-charter",
        type=Path,
        default=Path("configs/acceptance/travel-time-v1.2.yaml"),
    )
    qualify_day.add_argument(
        "--runtime-root",
        type=Path,
        default=Path("artifacts/runtime/milestone-0"),
    )
    qualify_day.set_defaults(handler=_data_qualify_day)

    gate = commands.add_parser("gate")
    gate.add_argument("--milestone", required=True, type=int)
    gate.add_argument("--report-root", type=Path, default=Path("artifacts/reports/gates"))
    gate.add_argument("--acceptance-version", default=DEFAULT_ACCEPTANCE_VERSION)
    gate.set_defaults(handler=_gate)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run one Arrive90 command and fail with a concise user-facing error."""

    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "milestone", 0) < 0:
        parser.error("milestone must be nonnegative")
    try:
        handler = args.handler
        return int(handler(args))
    except (InventoryError, OSError, ValueError) as error:
        print(f"arrive90: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
