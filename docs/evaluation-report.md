# Evaluation report

## Evidence boundary

This report separates source feasibility, software correctness, synthetic protocol qualification, browser behavior, performance, security, and empirical transit outcomes.
Only the first six categories have local evidence.
There is no accepted empirical MBTA reliability result.

The acceptance charter is `v1` with SHA-256 `0d07788d7007f5f90d9bae54742bfc3d589ddbde493c88f21572f1249163d49c`.
Its status remains `UNFROZEN_SOURCE_GATE_FAILED`.

## Milestone status

| Milestone | Gate status | What passed locally | What prevents acceptance |
| --- | --- | --- | --- |
| 0 | `FAILED` | Public index, schema, transformation, and license audit. | Primary Vehicle Position boarding evidence and product-availability provenance are not identifiable. |
| 1 | `INSUFFICIENT_EVIDENCE` | Immutable archives, collectors, temporal views, alert revisions, and completeness mechanics. | Milestone 0 and primitive historical Vehicle Positions. |
| 2 | `INSUFFICIENT_EVIDENCE` | Candidate contracts, query generation, audit enumeration, simulation, pinned OTP, and synthetic graph smoke. | Accepted historical archive, frozen scope, full graph, and empirical recall. |
| 3 | `INSUFFICIENT_EVIDENCE` | Interval outcomes, censoring bounds, baselines, feature parity, and leakage rejection. | Accepted candidates, primary outcome, full resolution gate, and fitted baselines. |
| 4 | `INSUFFICIENT_EVIDENCE` | AFT mechanics, CDF and quantiles, monotone calibration, support discovery, transfer candidates, and immutable registry. | Chronological population, selected fitted models, calibration, support, and fresh-process production artifact. |
| 5 | `INSUFFICIENT_EVIDENCE` | Decision kernel, recovery kernel, API, capabilities, trip state, SSE, and bounded latency workload. | Accepted production model and empirical scenarios. |
| 6 | `INSUFFICIENT_EVIDENCE` | Frozen evaluation mechanics, fresh-process synthetic reproduction, censoring bounds, bootstrap, Pareto output, and bounded performance. | Accepted source, final output support, and unopened empirical final test. |
| 7 | `INSUFFICIENT_EVIDENCE` | Four Chromium workflows, accessible no-map path, synthetic screenshot, trip workflow, and explicit degraded states. | Accepted Milestone 6, immutable historical replay, and independent eight-person comprehension gate. |
| 8 | `INSUFFICIENT_EVIDENCE` | Create-only 28-day shakeout and 56-day panel mechanics, complete synthetic denominator, lineage, uncertainty, and nonserving shadow policy. | Accepted source and model plus real 28-service-day and 56-service-day collection. |
| 9 | Not accepted | Local reliability, security, backup, restore, license, documentation, and clean-checkout work packages. | Every prior gate plus the final repository and clean-checkout evidence report. |

The machine-readable gate reports are under [artifacts/reports/gates](../artifacts/reports/gates).
Each report retains its own failing checks, input hashes, exact missing prerequisite, and resume procedure.

## Correctness evidence

The local Python suite contains 234 tests and passes with 91.20 percent branch-aware coverage under the repository configuration.
It includes unit, property-like invariant, integration, concurrency, seeded-failure, causal-boundary, model-mechanics, API, persistence, and evaluation tests.

The targeted Milestone 9 reliability qualification runs 36 tests and passes all declared controls.
It covers source and model fallback, router failure, database failure, SSE slot recovery, clock regression, immutable resource maxima, expiry cleanup, authorization flooding, audit redaction, sink failure, backup, restore, tamper rejection, rate-limit concurrency, and the public API path.
The retained artifact is [milestone-9-reliability.json](../artifacts/reports/qualification/milestone-9-reliability.json).

The Chromium qualification passes four workflows with no unexpected, skipped, or flaky result.
It covers direct target-not-met behavior, transfer and schedule-only recovery, stale and abstained states, sparse and unsupported targets, future-ready suppression, ready-time normalization, keyboard landmarks, and no-map use.
The retained artifact is [milestone-7-browser.json](../artifacts/reports/qualification/milestone-7-browser.json).

## Seeded negative controls

| Seeded defect | Intended failure | Nearby control |
| --- | --- | --- |
| Future event requested before product availability | Temporal boundary rejects leakage. | Same event is available at or after its evidenced availability time. |
| Outcome module imported by feature construction | Architecture boundary fails. | Registered causal features build without outcome imports. |
| Reversed or inconsistent arrival bounds | Outcome contract rejects the row. | Valid interval and right-censored rows pass. |
| CDF reversal larger than `1e-12` | Model validation fails. | Monotone grid and permitted numerical correction pass. |
| Model, candidate, support, or API hash mismatch | Immutable registry load fails. | Exact bundle hashes load in a fresh process. |
| Replayed or concurrent decision capability | At most one trip can be created. | First exact recommendation consumption succeeds. |
| Unauthorized public trip identifier flood | Authorized rate budget is unchanged. | Matching bearer request succeeds. |
| Database or SSE persistence fault | Constant-shape failure and stream slot release. | Restored database path accepts the next request. |
| Regressed wall clock | Service returns a safe 503. | Monotonic clock recovery restores service. |
| One scheduled prospective query omitted | Complete-denominator check fails. | All 3,096 synthetic scheduled queries pass. |
| Seeded high-severity scan result | Security qualification returns `FAILED` for the repository finding. | Empty high and critical finding set passes. |

## Performance evidence

Correctness tests and performance measurements are separate.
The numbers below are bounded mechanics measurements rather than empirical transit results or production capacity claims.

The reference allocation is Linux ARM64 with four cgroup CPUs and 8,307,167,232 cgroup memory bytes.
The exact Python base is `python@sha256:78098ea6a3a9c6a7727a5d4674e4a44e57e01fac878ee9cb4d24a86bd93916ff`.
The measured image used Python 3.12.14.

| Workload | p95 | Iterations or scale |
| --- | ---: | --- |
| Warm cached schedule-only API search | 7.095403 ms | 200 requests. |
| Slowest initial-decision cell | 0.722044 ms | 500 iterations for each candidate-count and feed-state cell. |
| Recovery selection | 0.710128 ms | 500 iterations over ten candidates. |
| Ten-candidate normalization | 0.172168 ms | 500 iterations. |
| One-day replay generation | 0.515503 ms | 1 base query and 36 deadline variants. |
| One-month replay generation | 8.561916 ms | 31 base queries and 1,116 deadline variants. |
| One-year replay generation | 112.893748 ms | 365 base queries and 13,140 deadline variants. |

The API report is [milestone-5-latency.json](../artifacts/reports/qualification/milestone-5-latency.json).
The candidate and replay report is [milestone-6-performance.json](../artifacts/reports/qualification/milestone-6-performance.json).

The replay workload contains one origin-destination pair, one query time per service day, one readiness horizon, and 36 deadline slacks.
It does not represent the complete MBTA query population.

## Synthetic evaluation mechanics

The Milestone 6 synthetic fixture validates a 2,000-replicate complete-service-day bootstrap, partially identified outcome bounds, fixed eligible cells, Holm correction, Pareto output, explorer fallback, and byte-identical fresh-process reproduction.
Its release mode is `HISTORICAL_EXPLORER` and its empirical status is `INSUFFICIENT_EVIDENCE` by construction.

The Milestone 8 synthetic protocol records all 3,096 scheduled queries across 56 constructed service-day blocks.
Its serving-band control contains 2,200 decisions and 1,100 distinct base queries.
Its nonserving 0.95 shadow control contains 896 decisions and 448 distinct base queries.
The measured synthetic policy half-width is `0.005917159763313609`, below the protocol control threshold of `0.03`.

Those values validate denominator, support, lineage, maturity, bootstrap, and precision code only.
They do not authorize historical or prospective calibration claims.
The artifact is [milestone-8-synthetic.json](../artifacts/reports/qualification/milestone-8-synthetic.json).

## Security and dependency evidence

Trivy 0.73.0 scanned Python and Node lock dependencies, repository secrets, Dockerfile configuration, licenses, and the exact release-candidate image.
Ruff security rules scanned packages, scripts, tools, and benchmarks.
The final retained qualification at this documentation checkpoint reported zero critical or high vulnerabilities, zero critical or high misconfigurations, zero secrets, and a non-root image user of `65532:65532`.

The compact report is [milestone-9-security.json](../artifacts/reports/qualification/milestone-9-security.json).
The license inventory resolves 40 Python lock packages and four Node lock packages in [licenses-v1.json](../artifacts/reports/qualification/licenses-v1.json).

## Empirical result

There is no empirical result to report.
No frozen historical query population, accepted production bundle, unopened final test, immutable historical replay, independent usability cohort, live shakeout, or real prospective shadow panel exists.

The only defensible conclusion is that Arrive90 implements and tests the required mechanics while having insufficient source evidence for a reliability recommendation claim.
The exact data prerequisite is documented in [source-feasibility.md](source-feasibility.md).
