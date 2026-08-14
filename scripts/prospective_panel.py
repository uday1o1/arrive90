"""Operate the create-only prospective shakeout and shadow-panel evidence store."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from arrive90_evaluation.prospective import (
    ImmutablePanelStore,
    attempt_from_dict,
    evaluate_panel,
    freeze_panel,
    inventory_from_dict,
    outcome_from_dict,
    panel_from_dict,
)


def _object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return value


def _write_create_only(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def _freeze(args: argparse.Namespace) -> None:
    panel = panel_from_dict(_object(args.panel))
    freeze_panel(panel=panel, shakeout_report=_object(args.shakeout_report))
    destination = ImmutablePanelStore(args.store).write_manifest(panel)
    print(destination)


def _record_attempt(args: argparse.Namespace) -> None:
    store = ImmutablePanelStore(args.store)
    destination = store.record_attempt(
        store.load_manifest(), attempt_from_dict(_object(args.input))
    )
    print(destination)


def _record_outcome(args: argparse.Namespace) -> None:
    store = ImmutablePanelStore(args.store)
    destination = store.record_outcome(
        store.load_manifest(), store.load_attempts(), outcome_from_dict(_object(args.input))
    )
    print(destination)


def _report(args: argparse.Namespace) -> None:
    store = ImmutablePanelStore(args.store)
    historical_path: Path = args.historical_report
    report = evaluate_panel(
        panel=store.load_manifest(),
        attempts=store.load_attempts(),
        outcomes=store.load_outcomes(),
        inventory=inventory_from_dict(_object(args.lineage_inventory)),
        historical_reference=_object(historical_path),
        historical_reference_hash=hashlib.sha256(historical_path.read_bytes()).hexdigest(),
        as_of_utc=args.as_of_utc,
        bootstrap_seed=args.bootstrap_seed,
        bootstrap_replicates=args.bootstrap_replicates,
    )
    _write_create_only(args.output, report)
    print(args.output)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    freeze = commands.add_parser("freeze", help="freeze a panel after its 28-day shakeout")
    freeze.add_argument("--panel", type=Path, required=True)
    freeze.add_argument("--shakeout-report", type=Path, required=True)
    freeze.add_argument("--store", type=Path, required=True)
    freeze.set_defaults(handler=_freeze)
    attempt = commands.add_parser("record-attempt", help="record one scheduled query attempt")
    attempt.add_argument("--store", type=Path, required=True)
    attempt.add_argument("--input", type=Path, required=True)
    attempt.set_defaults(handler=_record_attempt)
    outcome = commands.add_parser("record-outcome", help="record one matured outcome")
    outcome.add_argument("--store", type=Path, required=True)
    outcome.add_argument("--input", type=Path, required=True)
    outcome.set_defaults(handler=_record_outcome)
    report = commands.add_parser("report", help="write one immutable final panel report")
    report.add_argument("--store", type=Path, required=True)
    report.add_argument("--lineage-inventory", type=Path, required=True)
    report.add_argument("--historical-report", type=Path, required=True)
    report.add_argument("--as-of-utc", required=True)
    report.add_argument("--bootstrap-seed", type=int, required=True)
    report.add_argument("--bootstrap-replicates", type=int, default=2_000)
    report.add_argument("--output", type=Path, required=True)
    report.set_defaults(handler=_report)
    return root


def main() -> int:
    args = parser().parse_args()
    args.handler(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
