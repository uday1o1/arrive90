"""Unified Arrive90 command-line entry point."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from arrive90_data_contracts.gate_cli import run_gate

from arrive90_ingestion.inventory import (
    INVENTORY_URL,
    InventoryError,
    download_inventory,
    write_inventory_lock,
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

    gate = commands.add_parser("gate")
    gate.add_argument("--milestone", required=True, type=int)
    gate.add_argument("--report-root", type=Path, default=Path("artifacts/reports/gates"))
    gate.add_argument("--acceptance-version", default="travel-time-v1")
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
    except (InventoryError, OSError) as error:
        print(f"arrive90: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
