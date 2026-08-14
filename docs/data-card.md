# Data card

## Intended data use

Arrive90 is designed to evaluate station-to-station MBTA subway itineraries with at most one transfer.
Its historical design needs schedule versions, realtime Vehicle Position observations, Trip Update baselines, Service Alerts, stable trip and vehicle identity, platform observations, and independently evidenced product-availability timestamps.

No raw transit feed, normalized source row, rider identity, rider coordinate, model binary, or mutable database is committed to Git.
Small committed fixtures are synthetic.

## Audited sources

| Source | Intended role | Current status |
| --- | --- | --- |
| MBTA GTFS schedule | Stops, routes, trips, stop times, calendars, and canonical schedule simulation. | Contract and archive path implemented. |
| MBTA Rapid Transit Events 2022 | Primary historical label candidate with separate actual and prediction event types. | Full frozen audit failed per-train completeness and censoring gates. |
| Cornell Tech Bus Observatory | Independent historical Vehicle Position trajectory diagnostic. | Public 2024 archive inspected; compaction omits Trip Updates, cancellation states, fetch timestamps, and feed headers. |
| MBTA GTFS Realtime Vehicle Positions | Prospective boarding, transfer, destination, movement, and presence evidence. | Immutable collector implemented; future collection gates remain. |
| MBTA GTFS Realtime Trip Updates | Rider-visible prediction baseline and sensitivity analysis only. | Forbidden as a substitute for primary Vehicle Position evidence. |
| MBTA Service Alerts | Causal disruption features and route closure state. | Revision-aware archive path implemented. |
| Public MBTA LAMP historical rail export | Historical feasibility candidate. | Rejected for V1 primary boarding evidence because stop timestamp provenance is coalesced. |

The exact official archive identity, producer commit, semantics, LAMP comparison, and limitations are recorded in [source-feasibility.md](source-feasibility.md).
The full scan records 59 same-second semantic duplicates and 1,627 producer service-second discrepancies.
Evaluation uses the epoch event time and deterministic duplicate resolution, while retaining the anomaly counts in qualification evidence.

## Unit of analysis

The base unit is a server-owned query for one origin station, destination station, ready-to-board time, schedule version, and cutoff.
The frozen evaluation expands each base query across deadline variants while retaining the base-query relationship and fixed weight.
Candidate policies are complete zero-transfer or one-transfer itineraries rather than independent marginal legs.

## Outcome semantics

The primary arrival is a latent event represented by an interval `(L, U]`.
A deadline success is identified only when `U` is at or before the deadline.
A deadline failure is identified only when `L` is after the deadline.
A deadline within the interval remains unresolved.

A boarding event requires observed stop presence at or after virtual-rider readiness.
A downstream movement observation may upper-bound completion at a prior station, but it cannot prove that the train remained boardable there.
The V1 ready time is ready-to-board time at the platform, so the first-leg access margin is zero.

## Temporal availability

Arrive90 retains event time, source-observed time, pipeline-known time, product-available time, and later download time as different fields.
A retrospective event timestamp never proves historical product availability.
Corrections retain the event time and acquire a new knowledge and availability time.

Features pass through one point-in-time access boundary.
The leakage suite deliberately requests future events and fails those requests.

## Scope and exclusions

The acceptance charter proposes all subway routes, but it has no supported lines, stations, or transfer stations until the frozen aggregate audit rules are applied.
No empirical row is eligible for model selection, calibration, final testing, or a public reliability result.
The synthetic qualifications test contracts and failure behavior only.

V1 excludes buses, commuter rail, ferries, paratransit, two-transfer itineraries, door-to-door access, personal walking distributions, fare decisions, accessibility guarantees, and individual rider calibration.

## Quality and bias risks

Realtime feed gaps, stale snapshots, trip-identity ambiguity, canceled service, sparse disruptions, correlated delays, interval width, and incomplete platform continuity can all change coverage.
The protocol therefore keeps unresolved mass in denominators, reports best-case and worst-case bounds, uses complete-service-day bootstrap blocks, and freezes slice eligibility before final-test access.

The absence of rider demographics prevents demographic fairness claims.
The station-to-station boundary and fixed walking assumptions prevent personal mobility or accessibility claims.

## Retention and privacy

Source feeds and derived artifacts live outside Git under content-addressed or create-only storage rules.
Runtime trip state expires and is deleted no later than six hours after creation.
Decision capabilities expire after ten minutes.
The service stores no rider name, account, contact detail, or coordinate.

## License and attribution

MassDOT provides the transportation data.
Arrive90 is independent and is not affiliated with or endorsed by MassDOT or MBTA.
The reviewed terms, required attribution, source hash, and redistribution matrix are in [../DATA_LICENSE.md](../DATA_LICENSE.md).
The locked software dependency inventory is in [../artifacts/reports/qualification/licenses-v1.json](../artifacts/reports/qualification/licenses-v1.json).

## Current gate

Milestone 0 remains `FAILED` after the full frozen audit.
The audit scanned 24,565,356 rows and resolved 25 of 975 candidate policies, for 2.5641 percent resolution and 97.4359 percent censoring.
No proposed line passed the retention gate.
All 25 resolved intervals passed the 300-second interval-width rule, so source completeness rather than interval precision is the limiting evidence.
The official 2022 archive resolves the label-provenance question, while its missing file time forces historical features to remain schedule-only and its missing cancellation evidence prevents complete first-eligible-train reconciliation.
