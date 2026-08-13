# Static routing and historical query population

Arrive90 uses OpenTripPlanner only to discover and serve static route-pattern candidates.
The project-owned contracts normalize router responses before candidates reach feature generation, scoring, or policy selection.
Trip identifiers and departure alternatives do not define the immutable route-policy key.
Route patterns, directions, boarding and alighting platforms, ordered stop sequences, and frozen transfer-walk rules do define that key.

The graph builder pins OpenTripPlanner 2.9.0 by a multi-platform image digest and records the corresponding upstream commit.
The official container contract mounts inputs at `/var/opentripplanner` and uses `--build --save` to create `graph.obj`.
Arrive90 runs the graph build with network access disabled, four CPUs, a 7 GiB memory limit, and a 6 GiB Java heap.
The build manifest hashes the GTFS archive, optional OSM extract, build configuration, router configuration, image, graph, and measured build interval.

Run a historical graph build into a fresh ignored directory:

```console
make build-otp-graph GTFS=/path/to/mbta-gtfs.zip OUTPUT=artifacts/graphs/mbta-2025
```

On macOS with Colima, the output and input paths must be inside a directory shared with the Colima virtual machine.
The repository and its ignored `artifacts/runtime` and `artifacts/graphs` paths satisfy that requirement in the audited development environment.
The build tool rejects a nonempty output directory so an older graph cannot be confused with the requested inputs.

The repository's synthetic qualification built a real two-pattern graph through the pinned linux/arm64 image.
That result proves only the container and configuration path, not MBTA graph completeness or production candidate recall.
The generated graph remains ignored and only the narrow qualification record is committed.

Canonical schedule simulation enforces the 90-minute initial departure window, zero or one transfer, exact platform order, pickup and drop-off eligibility, directed transfer-walk duration, and the 16-policy cap.
Departures with the same policy key deduplicate to the earliest canonical scheduled result regardless of router response order.
Distinct route patterns remain distinct.
The eligible-trip-set hash includes exact trip lineage and schedule timestamps separately from the route-policy key.

The static audit enumerator traverses only simple direct and one-transfer paths.
It is not a second production journey planner.
Its purpose is to define the recall denominator and expose an omitted supported route policy.
Transfers between different platforms require an explicit directed connectivity rule, and missing connectivity fails closed.

The historical population generator selects at most 12 station pairs per ordered route-pair and transfer stratum by keyed hash.
It generates every retained service date, every 30-minute query time from 06:00 through 23:00 local time, readiness horizons of zero, five, ten, and 15 minutes, and five-minute deadline slacks from five through 180 minutes.
Every base query has equal total weight, and its 36 deadline variants each receive one thirty-sixth of that weight.
Public request-lattice assignments use HMAC-SHA256 and balance independently inside each chronological split without any outcome input.
Source outages and incomplete observation windows are not accepted as query-generator inputs and therefore cannot remove difficult rows.

The exceptional-trip table excludes canceled, skipped, added, replacement, and non-revenue service from ordinary eligibility.
An unmatched trip censors the affected outcome.
A short-turned trip becomes eligible only when independent proof shows it served the complete candidate-policy path.

Milestone 2 acceptance remains `INSUFFICIENT_EVIDENCE` until the upstream source gate passes, the supported scope and transfer connectivity are frozen, a complete historical MBTA graph is built, the population includes at least 100 distinct station pairs, and full-population recall meets every overall and slice gate.
