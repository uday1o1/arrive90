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
    result = acquire_pinned_day(
        args.date,
        include_schedule=args.include_schedule,
        inventory_lock_path=args.inventory_lock,
        bus_profile_path=args.bus_profile,
        schedule_profile_path=args.schedule_profile,
        raw_root=args.raw_root,
        acquisition_lock_path=args.acquisition_lock,
    )
    print(json.dumps(result_payload(result), sort_keys=True))
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


def build_parser() -> argparse.ArgumentParser:
    """Build the frozen Milestone 0 CLI surface."""

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
    download.add_argument("--date", type=date.fromisoformat, required=True)
    download.add_argument("--include-schedule", action="store_true")
    download.add_argument("--inventory-lock", type=Path, default=DEFAULT_INVENTORY_LOCK)
    download.add_argument("--bus-profile", type=Path, default=DEFAULT_BUS_PROFILE)
    download.add_argument("--schedule-profile", type=Path, default=DEFAULT_SCHEDULE_PROFILE)
    download.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    download.add_argument("--acquisition-lock", type=Path, default=DEFAULT_ACQUISITION_LOCK)
    download.set_defaults(handler=_source_download)

    data = commands.add_parser("data")
    data_commands = data.add_subparsers(dest="data_command", required=True)
    qualify_day = data_commands.add_parser("qualify-day")
    qualify_day.add_argument("--date", type=date.fromisoformat, required=True)
    qualify_day.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    qualify_day.add_argument("--bus-profile", type=Path, default=DEFAULT_BUS_PROFILE)
    qualify_day.add_argument("--schedule-profile", type=Path, default=DEFAULT_SCHEDULE_PROFILE)
    qualify_day.add_argument("--acquisition-lock", type=Path, default=DEFAULT_ACQUISITION_LOCK)
    qualify_day.add_argument(
        "--acceptance-charter",
        type=Path,
        default=Path("configs/acceptance/travel-time-v1.1.yaml"),
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
