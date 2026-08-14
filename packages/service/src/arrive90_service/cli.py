"""Loopback-only entry point for the held-out replay explorer."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn

from arrive90_service.app import create_app
from arrive90_service.explorer import DEFAULT_CLAIMS, DEFAULT_DEMO_ROOT, DEFAULT_FINAL_REPORT


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    parser.add_argument("--demo-root", type=Path, default=DEFAULT_DEMO_ROOT)
    parser.add_argument("--final-report", type=Path, default=DEFAULT_FINAL_REPORT)
    parser.add_argument("--claims", type=Path, default=DEFAULT_CLAIMS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.host not in {"127.0.0.1", "::1", "localhost"}:
        print("the held-out replay explorer is loopback-only", file=sys.stderr)
        return 2
    if not 1 <= args.port <= 65_535:
        print("port must be from 1 through 65535", file=sys.stderr)
        return 2
    app = create_app(
        demo_root=args.demo_root,
        final_report_path=args.final_report,
        claims_path=args.claims,
    )
    origin = f"http://{args.host}:{args.port}"
    print(f"Arrive90 held-out replay explorer at {origin}", file=sys.stderr)
    uvicorn.run(app, host=args.host, port=args.port, access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
