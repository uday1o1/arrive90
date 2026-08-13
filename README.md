# Arrive90

Arrive90 is a risk-aware MBTA subway journey-planning research project.
It is designed to compare the static fastest itinerary with alternatives using calibrated estimates of arrival before a rider-supplied deadline.

The project is not affiliated with or endorsed by the Massachusetts Department of Transportation or the Massachusetts Bay Transportation Authority.
MassDOT is the provider of the transit data used by the project.
Arrive90 does not use MassDOT or MBTA logos or trademarks.

## Current evidence status

Implementation is at the Milestone 0 source-feasibility gate.
The current public historical subway export does not preserve the source identity of its coalesced stop timestamp, so it cannot yet support the plan's required direct Vehicle Position boarding evidence.
No arrival probability or reliability claim is accepted while that gate is failing.

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

See [BUILD_PLAN.md](BUILD_PLAN.md) for the complete project authority and [docs/source-feasibility.md](docs/source-feasibility.md) for the current source audit.
