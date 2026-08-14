# Limitations

## Evidence limitations

The controlling limitation is source provenance.
The public historical rail export exposes a stop timestamp formed by coalescing Vehicle Position and Trip Update fields but omits the discriminator needed to identify which source supplied a row.
It also does not provide independent historical product-availability lineage.

As a result, Arrive90 cannot truthfully construct the required primary boarding outcomes, freeze a supported scope, train the intended historical bundle, open the final test, or report a real reliability result.
Milestone 0 is `FAILED`, Milestones 1 through 9 are not accepted, and the project is not portfolio-ready under its own build plan.

## Product limitations

The default application is a loopback schedule-only demonstration.
It returns null arrival probability and quantiles, does not issue a trip-start capability, and cannot provide a real recovery recommendation.
The Chromium fixture injects synthetic score bundles solely to exercise the user-facing contract.

Arrive90 plans station-to-station journeys with at most one subway transfer.
It does not include access or egress walking, buses, commuter rail, ferries, paratransit, fares, two-transfer itineraries, native notifications, or accessibility-aware timing.
OpenTripPlanner pedestrian edges, when used, connect station platforms and do not establish a complete accessibility claim.

## Modeling limitations

The intended AFT and transfer models have not been trained or selected on an accepted MBTA population.
Synthetic calibration, output-support, and prospective controls validate software mechanics only.
They do not estimate future production calibration, rare-disruption behavior, or rider benefit.

Arrival observations are intervals rather than exact passenger events.
Even with primitive Vehicle Positions, a stop observation upper-bounds latent arrival unless stronger audited semantics exist.
Virtual boarding models observed train presence after readiness, not doors opening or an individual passenger entering.

Fixed walking and transfer assumptions do not reflect individual mobility, crowding, platform access, or accessibility needs.
No rider demographic data is collected, so demographic performance and fairness are unknown.

## Evaluation limitations

The recorded performance workloads are bounded synthetic mechanics on one ARM64 environment.
They are not city-scale throughput, concurrent rider load, wide-area network latency, or production capacity measurements.
The one-year replay benchmark uses one origin-destination pair, one query time per service day, one readiness horizon, and 36 deadline variants.

The browser qualification contains four scripted Chromium workflows and no independent participant evidence.
The required eight-person comprehension protocol has not been run.
The synthetic screenshot is not an immutable historical replay.

The prospective qualification contains 3,096 synthetic scheduled queries across a constructed 56-service-day panel.
No real 28-service-day shakeout or 56-service-day shadow panel has begun.
The nonserving 0.95 shadow policy remains ineligible for user-facing use.

## Operational limitations

The service is not authorized for non-loopback deployment.
The intended TLS proxy topology has tests and a threat model but no deployed-environment qualification.
The local SQLite design is appropriate for the bounded V1 state lifecycle, not a multi-region or high-availability service.

Backups protect bounded runtime state but do not replace immutable source, graph, feature, model, or evaluation artifact storage.
Ephemeral default HMAC keys invalidate sessions across restarts.
Operators must supply and rotate protected keys for any future persistent environment.

## Security limitations

The security report is a dated scan against the exact repository, lockfiles, vulnerability database, and local container image recorded in its artifact.
A later advisory or base-image change requires a new scan.
No automated scan proves absence of unknown vulnerabilities.

The project has no external penetration test or managed production secret store.
Browser extension compromise and host compromise are outside the application boundary.

## Required next evidence

Resume with archived primitive Vehicle Position observations that preserve stable identity, platform, status, observation time, separate Vehicle Position stop provenance, product-availability lineage, and per-train continuity.
Then rerun Milestone 0 and proceed sequentially through every frozen gate.
Do not reuse the current synthetic artifacts as empirical evidence or modify the existing acceptance version after outcome access.
