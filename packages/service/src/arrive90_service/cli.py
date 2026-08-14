"""Loopback-only service entry point."""

from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

import uvicorn

from arrive90_service.app import create_app
from arrive90_service.contracts import ServiceConfig
from arrive90_service.demo import LocalBlockedBackend
from arrive90_service.store import CapabilityTripStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    parser.add_argument(
        "--state",
        type=Path,
        default=Path("artifacts/runtime/local-api.sqlite3"),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.host not in {"127.0.0.1", "::1", "localhost"}:
        print("non-loopback startup is disabled before Milestone 9", file=sys.stderr)
        return 2
    if not 1 <= args.port <= 65_535:
        print("port must be from 1 through 65535", file=sys.stderr)
        return 2
    args.state.parent.mkdir(parents=True, exist_ok=True)
    host_header = f"{args.host}:{args.port}"
    origin = f"http://{host_header}"
    config = ServiceConfig(
        allowed_hosts=frozenset({host_header}),
        allowed_origins=frozenset({origin}),
        decision_keys=(("process-v1", secrets.token_bytes(32)),),
        active_decision_key_version="process-v1",
        trip_keys=(("process-v1", secrets.token_bytes(32)),),
        active_trip_key_version="process-v1",
    )
    store = CapabilityTripStore(args.state, config)
    app = create_app(
        backend=LocalBlockedBackend(),
        store=store,
        config=config,
    )
    print(
        f"Arrive90 local-only API at {origin}; learned reliability is source-gate blocked",
        file=sys.stderr,
    )
    uvicorn.run(app, host=args.host, port=args.port, access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
