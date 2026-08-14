"""Run the loopback-only synthetic browser fixture for end-to-end tests."""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn
from arrive90_service.app import create_app
from arrive90_service.browser_fixture import BrowserFixtureBackend
from arrive90_service.contracts import ServiceConfig
from arrive90_service.store import CapabilityTripStore


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--state", type=Path, default=Path(".cache/browser-fixture.sqlite3"))
    args = parser.parse_args()
    if not 1 <= args.port <= 65_535:
        raise ValueError("port must be from 1 through 65535")
    args.state.parent.mkdir(parents=True, exist_ok=True)
    origin = f"http://127.0.0.1:{args.port}"
    config = ServiceConfig(
        allowed_hosts=frozenset({f"127.0.0.1:{args.port}"}),
        allowed_origins=frozenset({origin}),
        decision_keys=(("browser-fixture", b"d" * 32),),
        active_decision_key_version="browser-fixture",
        trip_keys=(("browser-fixture", b"t" * 32),),
        active_trip_key_version="browser-fixture",
        search_limit_per_minute=1_000,
        trip_creation_limit_per_hour=1_000,
        state_limit_per_minute=1_000,
    )
    backend = BrowserFixtureBackend()
    app = create_app(
        backend=backend,
        recovery_backend=backend,
        store=CapabilityTripStore(args.state, config),
        config=config,
    )
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
