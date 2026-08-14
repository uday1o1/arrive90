# Arrive90

Arrive90 is a risk-aware MBTA subway journey-planning research project.
It is designed to compare the static fastest itinerary with alternatives using calibrated estimates of arrival before a rider-supplied deadline.

The project is not affiliated with or endorsed by the Massachusetts Department of Transportation or the Massachusetts Bay Transportation Authority.
MassDOT is the provider of the transit data used by the project.
Arrive90 does not use MassDOT or MBTA logos or trademarks.

## Current evidence status

Locally executable implementation work now reaches the Milestone 7 rider interface.
Acceptance remains at the Milestone 0 source-feasibility gate.
The current public historical subway export does not preserve the source identity of its coalesced stop timestamp, so it cannot yet support the plan's required direct Vehicle Position boarding evidence.
No arrival probability or reliability claim is accepted while that gate is failing.

The default local application therefore fails closed with schedule information and explicit insufficient-evidence states.
The committed Chromium demonstration uses a clearly labeled synthetic fixture and is not MBTA performance evidence.

## Local verification

Install the pinned Python and dependencies, then run the local CI-equivalent checks.

```sh
uv python install
uv sync --frozen
make check
```

Run a milestone gate with:

```sh
make gate MILESTONE=0
```

The gate exits nonzero whenever its machine-readable report is `FAILED` or `INSUFFICIENT_EVIDENCE`.

## Local rider interface

Start the loopback-only application with:

```sh
uv run arrive90-api --port 8000
```

Open `http://127.0.0.1:8000` in a browser.
The default backend exposes no accepted probability and does not permit trip start while the source gate is blocked.

Install and run the real-browser acceptance suite with:

```sh
make browser-install
make browser-test
```

The browser suite starts a separate synthetic fixture on loopback and exercises direct, transfer, recovery, stale, absent, sparse-support, unsupported-target, future-ready, normalization, keyboard, and no-map paths against the real API and session store.
Use `make check-all` to run both the Python CI-equivalent suite and Chromium workflows.

See [BUILD_PLAN.md](BUILD_PLAN.md) for the complete project authority and [docs/source-feasibility.md](docs/source-feasibility.md) for the current source audit.
See [docs/offline-evaluation.md](docs/offline-evaluation.md) for the frozen evaluation mechanics, [docs/comprehension-protocol.md](docs/comprehension-protocol.md) for the external usability gate, and [docs/replay-demonstration.md](docs/replay-demonstration.md) for the demonstration evidence boundary.
