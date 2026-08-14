"""Create and restore digest-bound Arrive90 SQLite backups."""

from __future__ import annotations

import argparse
from pathlib import Path

from arrive90_service.backup import create_backup, restore_backup


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup")
    backup.add_argument("--state", type=Path, required=True)
    backup.add_argument("--output", type=Path, required=True)
    backup.add_argument("--manifest", type=Path, required=True)
    backup.add_argument("--created-at-utc", required=True)
    backup.add_argument("--expire-before-epoch", type=float, required=True)
    restore = commands.add_parser("restore")
    restore.add_argument("--backup", type=Path, required=True)
    restore.add_argument("--manifest", type=Path, required=True)
    restore.add_argument("--output", type=Path, required=True)
    return root


def main() -> int:
    args = parser().parse_args()
    if args.command == "backup":
        manifest = create_backup(
            args.state,
            args.output,
            args.manifest,
            created_at_utc=args.created_at_utc,
            expire_before_epoch=args.expire_before_epoch,
        )
        print(manifest.backup_sha256)
    else:
        manifest = restore_backup(args.backup, args.manifest, args.output)
        print(manifest.backup_sha256)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
