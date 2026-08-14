"""Run the real network-free replay explorer for browser tests."""

from __future__ import annotations

import argparse

import uvicorn
from arrive90_service.app import create_app


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not 1 <= args.port <= 65_535:
        raise ValueError("port must be from 1 through 65535")
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
