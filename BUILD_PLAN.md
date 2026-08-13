# Arrive90 Build Plan

Status: implementation-ready plan.

Planning snapshot: 2026-08-13.

Arrive90 is a calibrated, risk-aware public-transit planner for riders with a real arrival deadline.
It compares the nominally fastest itinerary with alternatives that have a higher estimated probability of reaching the destination before the deadline.
It exposes transfer risk, extra buffer, feed freshness, model uncertainty, and a recovery option when conditions change.

The name is a working product name.
It must never be presented as a 90 percent guarantee.

## 1. Product thesis

Existing transit products and research already provide arrive-by planning, live estimates, reliability-aware routing, and transfer-risk methods.
Arrive90 is differentiated by making an auditable, calibrated deadline-arrival distribution the primary product object and by publishing the full point-in-time and decision-evaluation evidence behind it.

Arrive90 asks:

> Given only information available now, which feasible itinerary has the best reliability-time tradeoff for my deadline?

The defensible contribution is not a new generic transit router.
The contribution is an end-to-end ML product with:

- Strict point-in-time feature generation.
- Historical realized-operation intervals and right-censored outcomes.
- Immutable prospective GTFS Realtime snapshots.
- Calibrated transfer and deadline-success estimates.
- Decision-focused evaluation rather than ETA error alone.
- One coherent interval-censored latent arrival-time distribution that produces both deadline probabilities and arrival quantiles.
- Explicit stale-feed and unsupported-scope behavior.
- Deterministic live status updates, state-conditioned transfer estimates, and recovery recommendations.

## 2. Target user and primary journey

The first supported user is an MBTA subway rider traveling between named stations with no more than one transfer.
The user has a ready-to-board time at the origin station and a deadline at the destination station.

The primary journey is:

1. Select an origin station and destination station.
2. Enter a departure-ready time and arrive-by deadline.
3. Select an estimated reliability target such as 80, 90, or 95 percent.
4. Review the fastest itinerary and a reliability-aware alternative.
5. Inspect median and upper-quantile arrival times, transfer buffer, backup wait, feed age, and explanation codes.
6. Start a trip session.
7. Receive deterministic freshness, closure, and official Trip Update updates during the trip, while the initial deadline probability and arrival quantiles remain frozen to their original cutoff.
8. Receive a concrete recovery itinerary if the planned transfer becomes unlikely.

An example decision card is:

```text
Safer option
Estimated 91% chance of arriving by 8:45 AM
Leaves 4 minutes earlier than the fastest option
Avoids a 3-minute transfer buffer at Downtown Crossing
Live feed age: 22 seconds
```

The interface must label every probability as an estimate.
If no itinerary reaches the requested threshold, the product shows the highest estimated probability and says that the target was not met only when that candidate's own prediction band, every applicable deadline slice, and static initial ready-horizon rule pass.
If those gates fail, the product suppresses the unsupported probability and returns an explicit `INSUFFICIENT_EVIDENCE` schedule-only result.

## 3. Job-description evidence

The project selection used current official descriptions from the active SimplifyJobs new-graduate corpus captured on 2026-08-13 as the primary design input.
The retained crawl contained 307 usable descriptions, including 64 roles categorized as data, AI, or machine learning.

Across those 64 descriptions, keyword-presence signals were:

| Technology signal | Roles containing it |
| --- | ---: |
| Python | 45 of 64 |
| Machine learning | 42 of 64 |
| SQL or databases | 30 of 64 |
| LLM or generative AI | 30 of 64 |
| Data engineering | 22 of 64 |
| PyTorch | 20 of 64 |
| Cloud | 17 of 64 |
| Performance | 16 of 64 |
| C++ | 16 of 64 |
| APIs or microservices | 14 of 64 |
| Distributed systems | 10 of 64 |
| CUDA or GPUs | 7 of 64 |
| Testing or CI/CD | 6 of 64 |
| MLOps | 5 of 64 |

A purposive 16-role strong-company sample was manually coded for responsibility and proof themes.
The sample intentionally overweights technically demanding roles and is not a random labor-market survey.

| Repeated responsibility or proof signal | Roles containing it |
| --- | ---: |
| Performance, scale, efficiency, or cost | 16 of 16 |
| Product, user, customer, or business impact | 15 of 16 |
| Collaboration or technical communication | 14 of 16 |
| Distributed systems, platforms, or infrastructure | 14 of 16 |
| Evaluation, experimentation, validation, or monitoring | 13 of 16 |
| Production deployment or serving | 11 of 16 |
| End-to-end ownership or full lifecycle | 8 of 16 |
| Data pipelines, features, labels, or quality | 8 of 16 |
| Reliability or robustness | 7 of 16 |

Arrive90 demonstrates these signals through causal data engineering, probabilistic modeling, calibration, decision evaluation, a production scoring API, live-feed reliability, operational monitoring, and a complete rider workflow.
The plan intentionally excludes LLMs and GPUs because they do not improve the core problem.

## 4. Novelty boundary and prior work

Google Maps, Transit, and agency applications already offer arrive-by planning, real-time departures, itinerary comparison, and navigation.
Reliability-aware transit routing, chance-constrained planning, transfer-failure modeling, and backup itineraries also exist in research.

Arrive90 must not claim to invent transit planning or probabilistic routing.
Its credible contribution is a reproducible product implementation that makes calibrated deadline risk auditable and evaluates route choices against realized operations.

The GTFS Realtime `uncertainty` field does not define a complete calibrated probability distribution.
Arrive90 therefore learns and validates its own outcome estimates instead of treating that field as a probability guarantee.

Truthful public claims may include only after the corresponding acceptance gate passes:

- Point-in-time replay prevents future operational events from entering model features.
- Probabilities are calibrated on a later chronological window.
- Decision policies are evaluated on a frozen later test period.
- Deadline probabilities and displayed arrival quantiles come from the same monotone interval-censored latent arrival-time distribution.
- A prospective shadow panel tests the exact live pipeline.

Public claims may not include:

- Guaranteed arrival probabilities.
- Complete accessibility routing.
- Personalized walking-time accuracy.
- Historical comparison against rider-visible GTFS Realtime snapshots before those snapshots were collected.
- Generalization to other agencies, buses, or commuter rail.
- Causal claims about how a recommendation changes rider outcomes.

Before public release, create a dated competitor matrix covering Google Maps, Transit, Citymapper, the official MBTA application, and the reliability-routing papers in Section 36.
Record whether each source exposes a deadline probability, calibration evidence, decision evaluation, feed freshness, and recovery behavior.
If a market-wide novelty statement is not supported by that matrix, describe Arrive90 as a distinctive evidence-backed implementation rather than a previously unsolved product.

## 5. Strict V1 scope

### 5.1 Supported

- One agency: MBTA.
- Subway rail lines represented in the audited LAMP data.
- Named origin and destination stations.
- Ready-to-board time at the origin station.
- Arrival deadline at the destination station.
- Zero or one transfer.
- Conservative fixed transfer-walk values from validated GTFS transfer or pathway data plus documented margins.
- Historical schedules active on each service date.
- Historical realized rail events from LAMP.
- Prospective GTFS Realtime vehicle positions and versioned service alerts as V1 online-equivalent model inputs.
- Prospective GTFS Realtime trip updates as a displayed official estimate and deterministic baseline, but not as a learned V1 feature.
- Static, deterministic, empirical, point-model, and probabilistic baselines.
- Estimated transfer-success and deadline-success probability.
- Median and selected arrival quantiles.
- Deterministic trip-status updates, supported conditional transfer estimates, and recovery decisions through server-sent events.
- Recovery itinerary generation.

### 5.2 Explicitly unsupported in V1

- Bus and commuter-rail uncertainty modeling.
- Door-to-door origin and destination addresses.
- Learned personal walking speed.
- More than one transfer.
- Fare optimization.
- Crowding prediction.
- Native mobile applications.
- Push notifications.
- User location history.
- Account-based personalization.
- Multi-agency routing.
- LLM-generated explanations.
- Deep neural networks without a measured structured-model failure.
- Safety guarantees.
- Complete wheelchair-route claims based on incomplete metadata.

OpenTripPlanner may still use pedestrian transfer edges needed between connected station platforms.
The product must label the station-to-station boundary clearly.

## 6. Portfolio acceptance definition

The project is portfolio-ready only when all of the following are true:

- A clean checkout can build the data sample, models, API, router, and web application through documented commands.
- The data audit passes the label-provenance, coverage, and causal-availability gates in Section 6.1.
- Every training feature is generated through a point-in-time access boundary.
- Deliberate future-event access fails a temporal-leakage test.
- Baseline and candidate models train from immutable manifests.
- The final test interval is not used for model, feature, threshold, or calibrator selection.
- Calibration and decision metrics include block-bootstrap confidence intervals.
- Every displayed transfer probability was frozen eligible before final-test access and has passing candidate-population decile and transfer-station artifacts, and every probability-based recovery trigger additionally was frozen eligible and has a passing selected-policy decile artifact.
- A recovery decision is a deterministic schedule action whose deadline probability and arrival quantiles are null and whose response makes no reliability-target claim.
- The web product shows fastest and safer itineraries with honest degraded states.
- Post-start SSE updates never rescore the initial arrival CDF outside its temporal support domain and preserve explicit provenance for every deterministic or state-conditioned value.
- Every trip-startable decision capability is single-use, digest-stored, memory-only, non-cacheable, redacted, Origin-protected, and expired within ten minutes.
- Trip reads, mutations, stops, and SSE streams enforce the bearer, transport, Origin, input-bound, rate-limit, and log-redaction contract before any non-loopback portfolio release.
- Stale or unavailable feeds never appear as fresh live guidance.
- A prospective collector has passed the 28-service-day operational shakeout and the power-based shadow evidence gate before a live-calibration claim is made.
- Published claims distinguish historical replay from prospective live shadow results.
- Model cards, data cards, limitations, and reproduction steps are complete.

An offline-validated repository and recorded local demonstration may be published after Milestone 7 using retained historical replay and clearly labeled offline evidence.
The web application remains loopback-only until Milestone 9 passes, so Milestone 7 never authorizes a public non-loopback deployment.
It may not claim prospective live calibration until Milestone 8 passes.

### 6.1 Versioned acceptance charter

Before Milestone 1 begins, commit `configs/acceptance/v1.yaml` and its human-readable explanation.
The charter contains the supported lines and stations, contiguous historical date rule, aggregate scope-exclusion rules, arrival-interval and boarding-observation rules, query-sampling seed, initial server-owned query-cutoff rule, ready-time normalization, conservative deadline-grid normalization, and live-horizon fallback rules, chronological split boundaries or the deterministic rule that will produce them, candidate-recall threshold, decision-context and alert-mask version, censoring thresholds, complete-band deadline and transfer calibration rules, transfer-model selection and weighting, candidate and selected-transfer support rules, slice-calibration support rules, the complete output-support eligibility manifest rule, model-selection rule, calibration tolerances, exact initial and recovery decision-policy ordering, exact trip-state transition graph, performance hardware, and prospective precision rule.
It also freezes the primary statistical hypothesis, every secondary hypothesis, the multiple-comparison correction, and the exact numerator, denominator, and weighting rule for every acceptance metric.

The initial non-negotiable gates are:

- Primary boarding, transfer, and destination events use independently identifiable Vehicle Position evidence rather than a coalesced Trip Update prediction.
- The preferred primary mode is direct `VP_STOPPED_AT` evidence when source provenance is recoverable.
- If that provenance is absent, the only permitted primary fallback is a conservative `VP_DEPARTED_STATION_UPPER_BOUND` derived from the next stop's Vehicle Position move event, and public outputs must label the resulting probability as conservative.
- A Vehicle Position `STOPPED_AT` timestamp is the time of an observation and therefore upper-bounds the latent arrival time unless an audited source proves exact event semantics.
- A downstream Vehicle Position move timestamp may upper-bound an arrival or station-completion time, but it never proves that a train remained observable at an earlier station.
- Every destination observation records a lower and upper arrival bound rather than silently treating either Vehicle Position timestamp as an exact arrival.
- For a deadline `d`, success is identified only when `arrival_upper_bound_utc <= d`, failure is identified only when `arrival_lower_bound_utc > d`, and any deadline inside the interval remains unresolved.
- Every boarded leg requires an observed `STOPPED_AT` event at its boarding platform at or after virtual-rider readiness or a direct stop-presence interval that proves the train remained observed at the platform after readiness.
- The evidence status is named `OBSERVED_STOP_PRESENCE_AFTER_READY`, and the benchmark states explicitly that boardability is a virtual-rider assumption rather than a measurement of doors or passenger acceptance.
- The public input is ready-to-board time at the platform, so the V1 first-leg access margin is exactly zero.
- The Milestone 0 `audit_candidate_resolution_rate` denominator is every manually enumerated candidate policy for the frozen audit queries, and its numerator is every candidate whose complete journey is either `ARRIVED` or `PROVEN_NO_ARRIVAL_WITHIN_HORIZON` under primary evidence.
- `audit_candidate_resolution_rate` is at least 90 percent overall and at least 80 percent in every proposed line-by-peak-or-off-peak slice.
- No more than 10 percent of frozen audit candidate policies are censored overall.
- At least 90 percent of audit `ARRIVED` outcomes overall and at least 80 percent in every proposed line-by-peak-or-off-peak slice have an arrival interval no wider than 120 seconds in `VP_STOP_OBSERVATION_INTERVAL` mode or 300 seconds in `CONSERVATIVE_STATION_DEPARTURE_INTERVAL` mode.
- Interval-width coverage uses every audit `ARRIVED` outcome in its denominator and cannot discard a wide interval.
- The Milestone 3 `full_candidate_resolution_rate` uses the same numerator and denominator definitions over every generated candidate in the complete frozen historical query population and must meet the same overall and slice thresholds.
- The Milestone 6 `primary_pair_resolution_rate` denominator contains every base-query deadline variant in the frozen final test with its frozen weight, and its numerator contains every variant for which both the primary Arrive90 policy and the static fastest-candidate comparator have resolved primary outcomes.
- `primary_pair_resolution_rate` is the sum of frozen weights in that numerator divided by the sum of frozen weights in that denominator.
- `primary_pair_resolution_rate` is at least 90 percent overall and at least 80 percent in every published line-by-peak-or-off-peak slice.
- Candidate generation recovers at least 99 percent of eligible zero-transfer and one-transfer route patterns found by the audit enumerator on the frozen fixture corpus.
- The fixed deadline-probability bands are `[0.00, 0.10)`, `[0.10, 0.20)`, `[0.20, 0.30)`, `[0.30, 0.40)`, `[0.40, 0.50)`, `[0.50, 0.60)`, `[0.60, 0.70)`, `[0.70, 0.80)`, `[0.80, 0.90)`, `[0.90, 0.95)`, and `[0.95, 1.00]`.
- Deadline prediction-band membership uses only the six-decimal round-half-to-even policy probability defined in Section 18, including exact left-closed and right-open boundary comparison except for the final right-closed band.
- Prediction-band membership is assigned from every initial selected-policy decision in the frozen public request lattice before its outcome is resolved, and unresolved decisions never disappear from a band denominator.
- For each band, `predicted_mean` is the frozen-weighted mean unrounded calibrated prediction over every initial selected-policy decision assigned by the rounded policy value, `success_lower` assigns every unresolved outcome zero, and `success_upper` assigns every unresolved outcome one.
- The band calibration bound is `max(abs(predicted_mean - success_lower), abs(predicted_mean - success_upper))`.
- Every band below 0.95 is eligible to display a numeric deadline probability only when the upper 95 percent complete-service-day block-bootstrap confidence bound for that worst-case calibration bound is no greater than 0.05.
- Every displayed band below 0.95 contains at least 500 distinct initial selected-policy decisions from at least 250 distinct base queries spanning at least 50 service-day blocks.
- Every supported line, origin parent station, destination parent station, and transfer parent station that can appear in any numeric model-based recommendation must contain at least 250 initial selected-policy decisions from at least 125 distinct base queries spanning at least 30 service-day blocks across all currently display-eligible bands.
- An initial selected-policy decision belongs to every line traversed and to each origin, destination, and transfer parent station used by its returned itinerary, so a multi-line itinerary may not be assigned only to its strongest slice.
- The upper 95 percent complete-service-day block-bootstrap confidence bound for the worst-case calibration bound is no greater than 0.08 for each such line and parent-station slice.
- Before final-test access, a deadline band, deadline slice, or target-specific cell that misses an applicable pre-test rule is frozen as ineligible, suppresses the affected numeric deadline probability or target claim, returns `INSUFFICIENT_EVIDENCE`, and may not emit `TARGET_MET`, even when a broader cell passes.
- The complete deadline eligibility submanifest is hashed with the decision policy before final-test access and cannot change in response to a final-test outcome.
- A deadline band, deadline slice, or target-specific cell frozen as eligible that later misses a final-test gate fails the frozen policy rather than becoming post-test ineligible.
- Recovering from that failure requires a new acceptance version, a newly frozen policy, and an untouched future test interval, while the original failed result remains immutable.
- The `[0.95, 1.00]` band cell and the target-specific 0.95 cell are published only when a prior frozen prospective evidence panel contains at least 800 distinct `SHADOW_095_EVIDENCE_V1` recommendations whose canonical rounded deadline probability is in `[0.95, 1.00]`, from at least 400 distinct base queries spanning at least 56 service-day blocks, and a later acceptance version passes those cells on an untouched future test interval.
- V1 declares no target-specific cell for 0.80 or 0.90, so those targets rely on the candidate's own band, applicable deadline slices, and the static initial ready-horizon rule.
- If the required prospective panel is unavailable when offline V1 freezes, both 0.95 cells are ineligible, and enabling either later requires a new acceptance version that treats the matured panel only as pre-test evidence and passes an untouched future final-test interval.
- The 0.95 calibration gap uses the same complete-band unresolved-outcome bound, and its upper 95 percent confidence bound must be no greater than 0.03.
- The transfer classifier is trained and calibrated on one deadline-independent row per transfer candidate policy under Section 17.2 and never duplicates a row for generated deadline variants.
- Every conditional transfer probability is rounded to six decimals with decimal round-half-to-even before decile lookup or the below-0.50 trigger comparison.
- Every scored eligible transfer-candidate decision that has reached the frozen `AT_TRANSFER` boundary is assigned by that rounded value to one fixed probability decile `[0.0, 0.1)`, `[0.1, 0.2)`, through `[0.9, 1.0]` before its second-leg transfer outcome resolves and retains its frozen `w / K_transfer` weight.
- Within each occupied transfer-probability decile, `transfer_predicted_mean` uses the unrounded calibrated transfer prediction over rows assigned by the rounded value, while `transfer_success_lower`, `transfer_success_upper`, and the worst-case calibration bound use the same unresolved-outcome construction as deadline calibration.
- A numeric transfer probability is displayable for an itinerary only when its candidate-population decile contains at least 500 transfer decisions from at least 250 distinct base queries spanning at least 40 service-day blocks and the upper 95 percent complete-service-day block-bootstrap confidence bound for its worst-case calibration bound is no greater than 0.08.
- A transfer station may display a numeric probability only when it has at least 800 transfer decisions from at least 400 distinct base queries spanning at least 50 service-day blocks across all deciles and its frozen-weighted complete-population expected calibration bound has an upper 95 percent confidence bound no greater than 0.08.
- The station expected calibration bound is the frozen-weighted mean across its candidate-population deciles of each decile's worst-case absolute calibration bound, with every unresolved transfer decision retained in its original decile.
- Every transfer itinerary selected by the confirmatory 0.90-target and 20-minute-cap policy for a deadline variant that reaches `AT_TRANSFER` creates one selected-policy trigger occurrence referencing its canonical deadline-independent transfer row and is retained in a selected-policy decile at its server-equivalent transfer decision time.
- A trigger occurrence carries that deadline variant's full primary weight, while the canonical classifier and candidate-population transfer row remains unique and retains only its frozen `w / K_transfer` weight.
- Repeated selections of one canonical transfer row across deadline variants are correlated decision occurrences rather than duplicated training examples, retain distinct deadline-variant keys, and remain in the same complete-service-day bootstrap block.
- The rounded-probability-below-0.50 recovery trigger is enabled only for a trip initiated from the confirmatory 0.90-target and 20-minute-cap policy and only when its selected-policy decile contains at least 300 selected transfer decisions from at least 150 distinct base queries spanning at least 30 service-day blocks and its worst-case calibration-bound upper confidence limit is no greater than 0.08.
- Before final-test access, apply the preceding support and calibration rules to locked pre-test evidence and freeze every candidate-population decile, transfer station, and selected-policy trigger decile as eligible or ineligible.
- A transfer cell frozen as pre-test-ineligible returns `INSUFFICIENT_EVIDENCE`, omits the affected numeric transfer probability or disables the probability-based trigger, and does not disable a causally known closure trigger.
- A transfer cell frozen as eligible that later misses an applicable final-test support or calibration gate fails the frozen policy rather than becoming post-test ineligible or being selectively suppressed.
- Reports show total, resolved, and unresolved decisions, distinct base queries, service-day blocks, weighted mass, and cluster-adjusted effective sample size separately.
- Reports show arrival-interval width and the observable source-observation-to-product-availability lag by line, station, evidence mode, and selected policy.
- The report states that neither quantity identifies the unknown latent-arrival-to-first-observation delay, so it cannot claim to subtract instrumentation delay from model error.
- For each quantile level exposed by the UI, compute the complete-population lower and upper empirical coverage from Section 22.2 before outcomes are filtered.
- A quantile level is displayable only when at least 1,000 initial selected-policy decisions from at least 500 distinct base queries and 50 service-day blocks support it and the upper 95 percent block-bootstrap confidence bound of `max(abs(tau - coverage_lower), abs(tau - coverage_upper))` is no greater than 0.08.
- Before final-test access, apply the preceding support and coverage rule to locked pre-test evidence and freeze every quantile level as eligible or ineligible.
- A quantile level frozen as pre-test-ineligible is omitted and reported as `INSUFFICIENT_EVIDENCE`, even when another quantile level or target-probability band passes.
- A quantile level frozen as eligible that later misses an applicable final-test support or coverage gate fails the frozen policy rather than being omitted after outcomes are opened.
- The complete output-support eligibility manifest contains every deadline band, deadline slice, declared reliability-target cell, transfer candidate-population decile, transfer station, selected-policy trigger decile, and quantile level with its pre-test evidence hashes and eligibility state.
- The complete output-support eligibility manifest is hashed with the decision policy before final-test access and cannot change in response to a final-test outcome.
- Any final-test failure of an output-support cell frozen as eligible fails the complete policy version, remains visible in its immutable report, and cannot be converted into a passing result by suppressing that cell.
- Recovering from any such failure requires a new acceptance version, a newly frozen output-support eligibility manifest, and an untouched future test interval, while the original failed result remains immutable.
- Pre-test eligibility discovery begins from one bytewise-ordered inventory in which every declared output-support cell is provisionally eligible.
- In iteration zero, the side-effect-free decision kernel treats every provisionally eligible cell as supported and materializes the complete initial selected-policy population for each deadline variant's frozen assigned public request-lattice member plus the transfer, trigger, and quantile evaluation populations from the locked pre-test evidence.
- Recovery actions are excluded from deadline-band, deadline-slice, target-cell, and quantile eligibility populations because recovery exposes no deadline probability, arrival quantile, or reliability-target claim and is evaluated separately under its frozen deterministic action policy.
- Every kernel worker reads the same immutable iteration-input manifest, and no metric computed during an iteration can change a support result until the next iteration.
- At the end of each iteration, evaluate every currently eligible cell against its applicable pre-test support, calibration, count, service-day, slice, target-specific, and coverage gates using only the populations materialized in that iteration.
- The iteration removal set contains every currently eligible cell that fails any applicable gate, is serialized in bytewise cell-identifier order, and makes all of its members ineligible simultaneously before the next kernel run.
- An ineligible cell is absorbing and can never become eligible again in the same acceptance version, even if later selection changes would make its diagnostic gate pass.
- Discovery reaches its stable fixed point when one complete iteration produces an empty removal set.
- With `N` declared cells, discovery terminates after at most `N` nonempty removal iterations plus one final empty-removal verification iteration because every nonempty iteration permanently removes at least one cell.
- The final verification iteration must reproduce the same decisions, evaluation populations, metrics, and empty removal set in a fresh process before the manifest can be frozen.
- The discovery artifact records the ordered cell inventory, initial all-eligible state, pre-test evidence hashes, acceptance-charter hash, decision-kernel algorithm hash excluding the final eligibility and discovery fields, every iteration's input manifest hash, initial-selected-population hash, metric hash, simultaneous removal set, output manifest hash, and the final verification hash.
- The final stable manifest and complete discovery-artifact hash are included in the decision-policy hash before final-test access.
- The single primary policy uses reliability target 0.90 and a 20-minute maximum-extra-time cap, and its single primary comparator is the static fastest-candidate policy on identical candidates and queries.
- The confirmatory policy, comparator, probability quantization, cap calculation, backup selection, recovery trigger precedence, recovery reference rules, trip-state graph, and every tie order are exactly the rules in Section 18 and are hashed into the decision-policy manifest before final-test access.
- The single primary estimand is the partially identified weighted difference in deadline-success probability between those policies over the complete frozen final-test base-query deadline-variant population, using each deadline variant's full frozen weight rather than its public-lattice support weight.
- The complete-service-day block-bootstrap lower 95 percent confidence bound for the worst-case missing-outcome bound of that primary difference is above zero.
- The primary Arrive90 policy has mean added planned time no greater than 10 minutes and no selected recommendation exceeds the frozen 20-minute cap, while P95 is reported as a diagnostic.
- Every predeclared secondary target, cap, comparator, or slice uses the frozen Holm familywise correction at alpha 0.05 before final-test access, while unregistered public combinations remain product inputs and descriptive calibration strata rather than post hoc hypotheses.
- Supported initial scoring, conditional transfer scoring, and deterministic recovery selection are each below 100 milliseconds p95, and a warm cached station search is below one second p95 on the named reference machine and frozen workload.
- At least seven of eight usability participants who did not build the product correctly answer that the displayed probability is an estimate rather than a guarantee and correctly identify a `TARGET_NOT_MET` result.
- If the interface receives its one permitted comprehension-driven revision, the gate is rerun with eight fresh participants who saw no earlier version.

If measured source coverage makes a line or transfer station fail the minimums, narrow the supported scope before query generation is frozen.
Do not lower a gate after model or policy results are visible.
A necessary protocol correction or post-test eligibility change creates a new acceptance version, preserves the failed result, invalidates downstream evidence produced under the older version, and requires an untouched future test interval before a revised policy can pass.

Every milestone writes `artifacts/reports/gates/milestone-N.json` with the acceptance-version hash, input-manifest hashes, command, result, and failing checks.
Every report status is exactly `PASSED`, `INSUFFICIENT_EVIDENCE`, or `FAILED`.
`make gate MILESTONE=N` exits nonzero when any required report has a status other than `PASSED`.
For Milestone 8, `INSUFFICIENT_EVIDENCE` is a successful report-generation outcome but `make gate MILESTONE=8` still exits nonzero and cannot unlock a prospective claim.

## 7. System architecture

```text
MBTA schedule archive and LAMP realized events
                  |
                  v
        immutable raw object store
                  |
                  v
      versioned normalization pipeline
                  |
                  +-------------------+
                  |                   |
                  v                   v
        point-in-time replay      outcome resolver
                  |                   |
                  v                   v
             feature store        label store
                  |                   |
                  +---- controlled training join
                            |
                            v
               training, calibration, and evaluation
                            |
                            v
                   immutable model registry

Live GTFS Realtime -> snapshot collector -> live state cache
Static GTFS + OSM -> OpenTripPlanner -> candidate itineraries
Candidates + live state + model -> risk and decision API
Risk API -> React station map and trip session through SSE
```

The architecture is intentionally small.
Kafka, Kubernetes, a distributed feature store, and a separate online model-serving platform are unnecessary for V1.

## 8. Proposed technology stack

- Python 3.12 or the newest compatible pinned stable release selected at Milestone 0.
- Polars, PyArrow, and DuckDB for Parquet inspection and offline transformations.
- XGBoost with a pinned `survival:aft` implementation for the interval-censored arrival-time distribution and an audited gradient-boosting implementation for classification baselines.
- scikit-learn for deterministic preprocessing, calibration, and baseline metrics.
- FastAPI and Pydantic for the risk and decision API.
- PostgreSQL for trip sessions, operational metadata, model metadata, and small serving tables.
- Filesystem storage locally and an S3-compatible object-store adapter for immutable raw and derived artifacts.
- OpenTripPlanner in a pinned container for candidate generation.
- React and TypeScript for the web product.
- MapLibre for the network map if licensing and bundle review pass.
- Server-sent events for one-way live trip updates.
- OpenTelemetry-compatible traces and Prometheus-style metrics.
- Docker Compose for the clean local path.
- `uv` and a committed Python lockfile.
- `pnpm` and a committed frontend lockfile.

Model selection must be evidence-driven.
If a regularized logistic or gradient-boosted baseline wins on the locked decision metrics, the simpler model remains the production model.

## 9. Repository layout

```text
arrive90/
  BUILD_PLAN.md
  README.md
  LICENSE
  DATA_LICENSE.md
  SECURITY.md
  CONTRIBUTING.md
  pyproject.toml
  uv.lock
  package.json
  pnpm-lock.yaml
  Makefile
  compose.yaml
  .env.example
  apps/
    collector/
      arrive90_collector/
      tests/
    api/
      arrive90_api/
      tests/
    web/
      src/
      tests/
  packages/
    data_contracts/
    ingestion/
    temporal_access/
    replay/
    features/
    labels/
    models/
    calibration/
    decision/
    evaluation/
    registry/
  configs/
    acceptance/
    data/
    features/
    models/
    decisions/
    evaluation/
  data/
    manifests/
    schemas/
    demo/
  artifacts/
    cards/
    reports/
  benchmarks/
  docs/
    architecture.md
    data-card.md
    model-card.md
    temporal-semantics.md
    evaluation-methodology.md
    operations.md
    limitations.md
    adr/
  scripts/
  tests/
    integration/
    end_to_end/
    leakage/
    shadow/
```

Large raw feeds, normalized Parquet partitions, models, and run outputs remain outside Git.
Small deterministic fixtures, manifests, schemas, expected reports, and cards belong in Git.

## 10. Authoritative data sources

### 10.1 MBTA LAMP

The MBTA LAMP public portal supplies:

- A compressed archive of issued MBTA GTFS schedules.
- Active and end dates needed to select the schedule valid on a historical service date.
- Daily subway on-time performance Parquet files.
- Realized rail events.
- Archived alert and static lookup tables where documented.

The Milestone 0 audit must read the current data dictionary and inspect real partitions.
Field names in this plan are expectations, not permission to assume undocumented semantics.
The public subway export may coalesce a Vehicle Position `STOPPED_AT` timestamp with a predictive Trip Update arrival timestamp.
The primary outcome path must not treat that coalesced field as realized ground truth unless the source provenance is independently recoverable.
The LAMP implementation and data dictionary must be compared because their documented Trip Update timestamp selection has differed.
When direct stop provenance is unavailable, LAMP's next-stop Vehicle Position move event may provide a defensible upper bound on the prior station's arrival.
The direct `STOPPED_AT` observation is also an upper bound unless the source audit proves that the timestamp represents an event rather than a position reading.
The lower bound is the latest earlier Vehicle Position observation that independently proves the train had not yet reached the stop, such as a correctly ordered `IN_TRANSIT_TO` or audited upstream observation for the same stable vehicle and trip.
This conservative proxy is permitted only as the separately named `VP_DEPARTED_STATION_UPPER_BOUND` outcome mode, and it excludes destinations without a defensible lower bound, the required downstream Vehicle Position event, and independent evidence that the destination was served rather than skipped.

### 10.2 GTFS and GTFS Realtime

GTFS Schedule is the authority for routes, trips, stops, stop times, calendars, transfers, and pathways.
GTFS Realtime is the authority for trip updates, vehicle positions, alerts, feed timestamps, and protobuf semantics.

Live ingestion must retain original protobuf bytes and the exact fetch timestamp.
Parsed tables do not replace the immutable source snapshot.

### 10.3 License and attribution

The implementation must comply with the MassDOT developer license.
The UI and repository must attribute MassDOT and MBTA as required.
The product must state that it is independent and not endorsed by or affiliated with MBTA.
Do not use protected MBTA marks as project branding.
Milestone 0 must record, source by source, whether raw objects, normalized rows, trained artifacts, screenshots, and aggregate metrics may be redistributed, retained, or only regenerated by the user.
The publication package must exclude every artifact whose redistribution right is absent or unresolved and must provide a permitted regeneration path instead.

## 11. Critical evidence separation

Published LAMP data provides realized outcomes and historical schedules.
It may not contain every historical prediction snapshot that a rider would have observed at query time.

The repository must therefore maintain two explicitly separate evidence tracks.

### 11.1 Historical causal replay

Historical replay may use:

- The most recently published schedule version whose knowledge time is no later than the simulated query and whose service records cover the service date.
- Primitive operational events whose event time and product-availability time are both no later than the simulated query.
- Historical alert revisions only when the exact revision and its publication or modification time prove that revision was known.
- Static station and transfer information active at the time.

It may not use future stop events, final trip counts, later nearest-schedule matches, next-stop-derived departure values, later alert revisions, post-journey aggregates, or later schedule corrections as features.
Filtering a finalized daily LAMP row by its event timestamp is not sufficient evidence of causal availability.
Every historical feature must have an online-equivalent derivation from primitive fields.

Milestone 0 creates a field-level provenance ledger before any historical operational feature is allowed.
Each ledger entry records the original field, source emission semantics, source-observation evidence, every LAMP transformation, every trip-matching dependency, earliest defensible online availability, future-sensitive dependency audit, and the exact offline and online derivations.
For historical primitives, `product_available_at_utc` means the earliest evidenced time that an online-equivalent consumer could have used the primitive, while `HistoricalSourceObject.downloaded_at_utc` separately records when Arrive90 later acquired the archive.
An event timestamp may not be copied into `product_available_at_utc` unless the current source specification or audited transformation code proves that an online consumer could have used that primitive at that time.
A retrospective match, correction, or daily publication time cannot be backdated to the operational event time.

The acceptance charter freezes `causal_feature_support_rate` for every operational feature family claimed by `historical_v1`.
The denominator is every eligible frozen audit candidate at its feature cutoff, and the numerator is every candidate with a provenance-valid value produced without fallback to a future transformation.
Recent delay, recent headway, and Vehicle Position progress each require at least 80 percent support overall and at least 60 percent support in every published line-by-peak-or-off-peak slice before they may be required production features.
Missingness indicators may remain, but a feature family that fails its support gate is removed from `historical_v1` and from public live-feature claims.
If no operational feature family passes, `historical_v1` becomes an explicitly schedule-only model or learned live recommendations wait for prospective training data.

### 11.2 Prospective live replay

Prospective replay may use immutable GTFS Realtime snapshots collected by Arrive90.
It evaluates exactly what the live product could have known at each snapshot time.

V1 uses live Vehicle Position primitives and versioned alerts only where the historical track contains the same online-equivalent feature semantics.
Trip Update predictions remain a named deterministic baseline and user-visible official estimate in V1.
A later `prospective_v2` learned schema may use Trip Update prediction features only after captured snapshots and matured labels provide independent chronological training, calibration, and final-test windows.

The final report must not merge the two result sets into one headline metric.

## 12. Versioned data contracts

### 12.1 Immutable feed blob and fetch attempt

```text
FeedBlob
  blob_sha256: content-addressed identifier
  content_type: string
  content_length: integer
  storage_uri: string
  first_seen_at_utc: timestamp
```

```text
FetchAttempt
  attempt_id: unique identifier
  parent_attempt_id: unique identifier or null
  agency_id: mbta
  feed_type: STATIC | TRIP_UPDATES | VEHICLE_POSITIONS | ALERTS
  source_object: stable source identifier
  fetched_at_utc: timestamp
  source_header_timestamp: timestamp or null
  maximum_entity_timestamp: timestamp or null
  http_status: integer or null
  blob_sha256: string or null
  parser_version: string
  schema_version: string
  feed_age_seconds: integer or null
  transport_status: SUCCEEDED | FAILED | TIMED_OUT
  parse_status: VALID | EMPTY | MALFORMED | NOT_PARSED
  semantic_status: VALID | INVALID | QUARANTINED | UNKNOWN
  freshness_status: FRESH | STALE | UNUSABLE | CLOCK_SKEW | UNKNOWN
```

HTTP failures are snapshot attempts and remain distinct from valid empty feeds.
Retries must not overwrite the original attempt record.
Identical bytes share one `FeedBlob` while retaining every separate `FetchAttempt` and fetch timestamp.

Historical LAMP and GTFS archive files use a separate immutable source-object contract because a daily derived file is not a rider-visible feed snapshot.

```text
HistoricalSourceObject
  source_object_id
  source_kind: LAMP_SUBWAY | LAMP_ALERTS | GTFS_ARCHIVE
  source_uri
  published_or_listed_at_utc or null
  downloaded_at_utc
  blob_sha256
  schema_fingerprint
  parser_version
```

### 12.2 Schedule record

```text
ScheduleStopTime
  schedule_version_id
  feed_version
  published_at_utc
  known_at_utc
  active_start_date
  active_end_date
  service_date
  service_id
  route_id
  direction_id
  trip_id
  block_id
  stop_id
  parent_station_id
  stop_sequence
  scheduled_arrival_local_seconds
  scheduled_departure_local_seconds
  pickup_type
  drop_off_type
  wheelchair_accessibility
```

Times beyond 24:00 follow GTFS service-day semantics and must not be parsed as the next civil date without the service-date context.
`known_at_utc` is the earliest verifiable public listing or publication time for that exact schedule version and must be no later than the historical query cutoff.
An active service date alone does not prove point-in-time publication.
If the archive cannot establish earlier public availability, the version remains unavailable before its later evidenced listing or acquisition time.
All schedule conversion uses `America/New_York` and the GTFS service-day definition, including daylight-saving transitions.

### 12.3 Realized stop event

```text
RealizedStopEvent
  service_date
  observed_trip_id
  planned_trip_id or null
  planned_match_status
  route_id
  direction_id
  stop_id
  parent_station_id
  stop_sequence
  arrival_lower_bound_utc or null
  arrival_upper_bound_utc or null
  arrival_interval_closed: LEFT_OPEN_RIGHT_CLOSED | EXACT | UNKNOWN
  arrival_evidence: VP_STOPPED_AT | VP_DEPARTED_STATION_UPPER_BOUND | VERIFIED_PAST_TRIP_UPDATE | PREDICTED_TRIP_UPDATE | UNKNOWN
  departure_lower_bound_utc or null
  departure_upper_bound_utc or null
  departure_interval_closed: LEFT_OPEN_RIGHT_CLOSED | EXACT | UNKNOWN
  departure_evidence: DIRECT_DEPARTURE | DOWNSTREAM_MOVE_UPPER_BOUND | UNKNOWN
  event_time_utc
  source_observed_at_utc or null
  pipeline_known_at_utc
  product_available_at_utc
  scheduled_arrival_utc or null
  scheduled_departure_utc or null
  travel_time_lower_bound_seconds or null
  travel_time_upper_bound_seconds or null
  dwell_time_lower_bound_seconds or null
  dwell_time_upper_bound_seconds or null
  quality_flags
  historical_source_object_id
  source_row_key
```

Ambiguous and unmatched trips must remain explicit.
They may be excluded under a documented rule, but they may not be silently forced to a planned trip.
Primary outcome resolution requires the evidence classes frozen in the acceptance charter.
Trip Update prediction fallback is retained for sensitivity analysis and is never silently promoted to realized evidence.
The acceptance charter selects exactly one primary arrival-evidence mode for a result set.
It may not mix direct arrivals and conservative upper bounds into one calibration headline.
For a realized event, the normal ordering is `event_time_utc <= source_observed_at_utc <= pipeline_known_at_utc <= product_available_at_utc` whenever all fields are present.
A correction retains its original event time but receives new pipeline-known and product-available times.
Records that violate the applicable ordering without an explicitly classified source clock-skew exception are quarantined from features and primary labels.

### 12.3.1 Boarding observation evidence

```text
BoardingObservationEvidence
  query_id
  itinerary_id
  leg_index
  observed_trip_id
  rider_ready_at_utc
  boarding_stop_id
  stopped_at_observation_utc or null
  stop_presence_lower_bound_utc or null
  stop_presence_upper_bound_utc or null
  evidence_status: OBSERVED_STOP_PRESENCE_AFTER_READY | DEPARTED_BEFORE_READY | AMBIGUOUS | MISSING
  evidence_source_row_keys
```

`OBSERVED_STOP_PRESENCE_AFTER_READY` requires a directly sourced `STOPPED_AT` observation at the boarding platform at or after rider readiness or a direct stop-presence interval that proves the train remained observed at the platform after readiness.
The GTFS Realtime status does not expose door state or prove actual passenger acceptance.
The virtual-rider oracle assumes that observed stop presence after readiness is boardable, and every public methodology view labels that assumption.
`DOWNSTREAM_MOVE_UPPER_BOUND` is never sufficient boarding observation evidence.

### 12.4 Historical query

```text
HistoricalQuery
  query_id
  query_time_utc
  service_date
  origin_station_id
  destination_station_id
  ready_at_utc
  deadline_utc
  observation_horizon_utc
  maximum_transfers = 1
  schedule_version_id
  query_generation_version
  sampling_stratum
  base_query_weight
```

The query contract enforces `query_time_utc <= ready_at_utc < deadline_utc`.
The feature cutoff is `query_time_utc` even when the rider will become ready later.
The V1 observation horizon is exactly 210 minutes after `ready_at_utc`, which exceeds the maximum accepted deadline by 30 minutes and is stored in every query and outcome manifest.

### 12.4.1 Initial live-query temporal contract

For every initial production search, the trusted API server captures `initial_query_cutoff_utc` from its own UTC clock immediately after transport, Host, and body-size checks and before semantic validation, candidate generation, or any schedule, feed, alert, feature, or model read.
That one timestamp becomes the production query's `query_time_utc`, the `DecisionContext.decision_cutoff_utc`, and the response `data_cutoff`.
The timestamp never advances because request processing, routing, scoring, or persistence finishes later.
Only schedule versions known by that cutoff and source records whose product-availability and fetch times are no later than that cutoff may enter the decision.
Any source record that arrives after the cutoff is unavailable to that search even when its event or source-header timestamp is earlier.

The raw requested `ready_at` may be up to two minutes earlier than `initial_query_cutoff_utc` only to tolerate client display rounding and request transit.
A raw requested time in that tolerance window is normalized to an `effective_ready_at` equal to `initial_query_cutoff_utc`, and the response returns both values with limitation code `READY_TIME_NORMALIZED_TO_CUTOFF`.
The model-supported initial ready lead is the closed interval from zero through 15 minutes after `initial_query_cutoff_utc`.
A request more than 15 minutes and no more than 24 hours ahead receives only the static-schedule candidate result under any causally known applicable alert mask, with `DEGRADED_SCHEDULE_ONLY`, `model_version = STATIC_SCHEDULE_BASELINE_V1`, `support_status = UNSUPPORTED_READY_HORIZON`, null model probabilities and quantiles, no learned live-feature claim, `decision_id = null`, `decision_expires_at = null`, and `trip_start_supported = false`.
A raw requested time earlier than the two-minute tolerance or more than 24 hours ahead fails before candidate generation.
The requested deadline must be from five through 180 minutes after `effective_ready_at` in every accepted branch.
The server computes `effective_deadline_at = effective_ready_at + 300 seconds * floor((requested_deadline_at - effective_ready_at) / 300 seconds)` and requires the resulting supported slack to remain from five through 180 minutes.
The effective deadline can never be later than the rider's requested deadline, and any changed value is returned with `deadline_time_status = NORMALIZED_DOWN_TO_SUPPORTED_GRID` and limitation code `DEADLINE_NORMALIZED_DOWN_TO_SUPPORTED_GRID`.
An unchanged deadline returns `deadline_time_status = AS_REQUESTED` and no deadline-normalization limitation.
Every model score, target comparison, selected-policy outcome, and trip snapshot uses `effective_deadline_at`, while `requested_deadline_at` is retained only for display and audit.
The common historical and model field `deadline_utc` always means `effective_deadline_at` and never aliases the raw requested timestamp.
V1 always generates both zero-transfer and one-transfer policies and exposes no request control that changes this candidate universe.
Client clocks never determine feature access, source selection, model-support eligibility, or the persisted decision cutoff.

### 12.5 Transit leg and candidate itinerary

```text
TransitLeg
  leg_index
  action_kind: FIRST_ELIGIBLE_ROUTE_PATTERN | SPECIFIC_TRIP_BASELINE
  schedule_version_id
  service_date
  canonical_static_trip_id or null
  route_discovery_static_trip_set_hash
  scheduled_journey_oracle_reconciliation_trip_set_hash
  scheduled_transfer_reconciliation_trip_set_hash or null
  route_pattern_id
  route_id
  direction_id
  boarding_stop_id
  boarding_parent_station_id
  alighting_stop_id
  alighting_parent_station_id
  boarding_stop_sequence
  alighting_stop_sequence
  scheduled_departure_utc
  scheduled_arrival_utc
  transfer_success_window_seconds or null
  router_estimated_departure_utc or null
  router_estimated_arrival_utc or null
  router_estimate_source_attempt_ids
  pickup_type
  drop_off_type
```

```text
CandidateItinerary
  itinerary_id
  query_id
  ordered_legs: ordered TransitLeg values
  scheduled_departure_utc
  scheduled_arrival_utc
  transfer_station_id or null
  scheduled_transfer_buffer_seconds or null
  conservative_transfer_walk_seconds
  backup_departure_identifiers
  graph_manifest_hash
  realtime_snapshot_hash or null
  candidate_generator_version
```

```text
DecisionContext
  decision_context_id
  query_id
  decision_cutoff_utc
  static_candidate_manifest_hash
  feature_row_manifest_hash
  alert_revision_source_keys
  alert_feed_blob_hashes
  alert_product_available_at_values
  candidate_eligibility_entries
  decision_context_version
```

Each candidate eligibility entry contains the immutable candidate policy key, `ELIGIBLE` or `INELIGIBLE`, and a frozen reason code such as `STATION_CLOSED`, `PLATFORM_CLOSED`, `PLATFORM_CHANGED_UNSUPPORTED`, or `ALERT_AMBIGUOUS`.
The static candidate universe never changes in response to a Vehicle Position, Trip Update, or alert.
Realtime evidence may mask a static candidate in the `DecisionContext`, but it may not add, remove, retime, or mutate the candidate manifest.
Every new alert revision produces a new decision context while retaining the original candidate manifest.
If every candidate is masked, the decision policy returns `NO_SUPPORTED_ITINERARY` with the exact context identifier and reason codes.
Historical replay and production scoring must reproduce identical eligibility entries from the same candidate manifest, alert revisions, product-availability cutoff, and decision-context version.

V1 evaluates `FIRST_ELIGIBLE_ROUTE_PATTERN` policies rather than pretending that a rider always waits for one scheduled trip instance.
For each leg, the canonical schedule simulation selects the first static revenue trip scheduled after the applicable readiness time that serves the exact boarding and alighting platforms in order and satisfies pickup, drop-off, and service-calendar rules.
That canonical trip anchors schedule features and does not restrict which delayed realized train the virtual rider may board.
The first-leg readiness time is exactly the query ready-to-board time, with a V1 first-leg access margin of zero.
Each later-leg readiness time is the canonical prior-leg arrival plus the frozen transfer-walk duration.
Ties use scheduled departure, scheduled arrival, stop-sequence tuple, and then bytewise `trip_id` order.
The complete route-discovery static-trip set, the scheduled journey oracle-reconciliation trip set, the scheduled transfer-reconciliation trip set when applicable, and the selected canonical trip are hashed into the candidate manifest.
For each leg, the scheduled journey oracle-reconciliation set contains every same-service-day revenue trip on the route pattern, including trips scheduled before readiness or after the frozen observation horizon, because V1 assumes no unaudited maximum-early-running bound.
For each transfer candidate, a separate transfer-reconciliation set contains every same-service-day revenue trip on the second-leg route pattern, including trips scheduled before transfer readiness or after `observation_horizon_utc + 900 seconds`.
Observed events determine whether a member can satisfy the applicable journey or transfer window, so inclusion in either safe superset never by itself makes the trip boardable or changes a label.
The candidate manifest hashes the journey and transfer reconciliation sets separately, and transfer labels may use only the frozen transfer-reconciliation set.
The scheduled departure, arrival, duration, and transfer buffer features always come from this canonical simulation.
Deadline slack is computed from the effective deadline and canonical scheduled times only in query support and decision-evaluation code, never in a model feature row.
Candidates with the same ordered route patterns, platform stops, transfer station, readiness rule, transfer-walk rule, route-discovery trip-set hash, and oracle-reconciliation trip-set hash are equivalent and must be deduplicated.
Duplicate OpenTripPlanner itineraries that name different departures collapse only after the canonical simulation proves that they represent the same policy state.
`SPECIFIC_TRIP_BASELINE` is reserved for the separately named official Trip Update baseline and never shares primary route-policy labels.
`historical_v1` uses only static-schedule candidate generation in historical evaluation, production scoring, and its first prospective shadow panel.
Its primary candidates require `realtime_snapshot_hash = null`.
Trip Update estimates may annotate those static route-pattern candidates or power the separate deterministic baseline, but they may not add, remove, or retime primary-policy candidates.

The frozen exceptional-trip decision table is:

| Realized condition | Oracle decision |
| --- | --- |
| Normal revenue trip with observed stop-presence evidence and required downstream stops | Eligible under the frozen virtual-rider boardability assumption. |
| Train already dwelling when the rider becomes ready | Eligible only when a direct `STOPPED_AT` observation occurs at or after readiness or a direct stop-presence interval proves continued observed presence. |
| `SKIPPED` boarding or alighting stop | Ineligible for that leg. |
| Short turn before the required alighting stop | Ineligible for that leg. |
| `CANCELED` trip | Ineligible. |
| Added or replacement trip | Eligible only when a point-in-time schedule relationship, revenue status, route, direction, platform sequence, and downstream service are independently identifiable. |
| Non-revenue movement | Ineligible. |
| Unmatched or identity-ambiguous trip that could be the first eligible train | The candidate outcome is censored rather than forced to a scheduled identity. |

A causally known station closure, platform closure, or unsupported platform change masks an affected static candidate in a new `DecisionContext` before scoring.
An already-issued recommendation is reevaluated only for deterministic eligibility under the new decision context and becomes unsupported when its selected policy is masked.
V1 does not rerank an active trip with the initial arrival CDF after its original cutoff, and a newly masked policy prompts the state-appropriate fresh search or recovery flow instead of applying stale probabilities to the new context.
Historical replay may apply such a change only when the exact revision was available by the feature cutoff.

### 12.6 Point-in-time feature row

```text
FeatureRow
  query_id
  itinerary_id
  feature_cutoff_utc
  route and direction features
  station and transfer features
  service-day and time features
  scheduled leg and itinerary duration
  recent observed delay features
  recent observed headway features
  latest online-equivalent Vehicle Position features when available
  active alert features when causally available
  feed, route-entity, candidate-feature, and alert-revision age and missingness flags
  feature_schema_version
  source_attempt_ids
  historical_source_row_keys
```

The feature package accepts a `TemporalView` that cannot return records after `feature_cutoff_utc`.
Model and training code must not receive the unrestricted outcome store.
The V1 registry rejects Trip Update prediction features because their historical training support is absent.
Every feature registry entry declares `event_time`, `product_available_at`, its exact online-equivalent derivation, and a seeded leakage fixture.

### 12.7 Outcome row

```text
OutcomeRow
  query_id
  itinerary_id
  first_boarding_observation_evidence_id or null
  transfer_boarding_observation_evidence_id or null
  destination_arrival_lower_bound_utc or null
  destination_arrival_upper_bound_utc or null
  deadline_label_status: SUCCESS_IDENTIFIED | FAILURE_IDENTIFIED | INTERVAL_UNRESOLVED | JOURNEY_CENSORED
  transfer_label_status: SUCCESS_IDENTIFIED | FAILURE_IDENTIFIED | WINDOW_CENSORED | NOT_APPLICABLE
  transfer_success: boolean or null
  deadline_success: boolean or null
  lateness_lower_bound_seconds or null
  lateness_upper_bound_seconds or null
  backup_used: boolean
  journey_status: ARRIVED | PROVEN_NO_ARRIVAL_WITHIN_HORIZON | CENSORED
  observation_complete_through_utc or null
  censoring_reason or null
  label_evidence_class
  outcome_time_semantics: VP_STOP_OBSERVATION_INTERVAL | CONSERVATIVE_STATION_DEPARTURE_INTERVAL
  oracle_policy_version
  outcome_resolved_at_utc
  outcome_version
```

Outcome tables live in a package and storage path not imported by feature or online-scoring modules.
For a finite destination interval, lateness bounds are `max(0, arrival_bound - deadline_utc)` applied to its lower and upper endpoints.
An unresolved or censored destination never receives a fabricated point lateness.

```text
ObservationCoverageWindow
  source_track: HISTORICAL_LAMP | PROSPECTIVE_GTFS_RT
  route_id
  direction_id
  window_start_utc
  window_end_utc
  expected_cadence_seconds or null
  maximum_observed_gap_seconds or null
  explicit_cancellation_or_closure_ids
  partition_quality_manifest_hash or null
  fetch_attempt_ids
  completeness_status: COMPLETE | INCOMPLETE | UNKNOWN
  completeness_reason
```

The absence of a stop-event row never proves non-arrival by itself.
A historical window is `COMPLETE` only when every train that could satisfy the frozen candidate policy during the horizon is reconciled to an explicit arrived, canceled, skipped, short-turned, non-revenue, departed-before-ready, or fully observed no-arrival state.
Route-level event density or the absence of a gap longer than two scheduled headways is a diagnostic and is never sufficient proof of completeness.
The reconciliation manifest records the expected or observed vehicle and trip identity, every relevant boarding and destination interval, the terminal state, and the source rows supporting that state.
An unaccounted vehicle, identity ambiguity, missing relevant stop interval, or unexplained trajectory gap makes the candidate observation window `INCOMPLETE`.
An explicit cancellation or closure may prove no eligible service only when its scope and availability cover every candidate-policy path through the horizon.
A prospective window is `COMPLETE` only when every scheduled fetch attempt is retained, valid fresh snapshots cover the interval with no gap longer than two collection cadences, source headers remain monotonic within the clock-skew policy, and the relevant route entities were observable.
If those conditions fail, an otherwise absent arrival is `CENSORED` rather than `PROVEN_NO_ARRIVAL_WITHIN_HORIZON`.

## 13. Ground-truth journey semantics

The project does not observe individual riders boarding.
It must define a deterministic virtual-rider oracle.

For each historical query:

1. The virtual rider is ready at the origin at `ready_at_utc`.
2. The rider follows the candidate's ordered route-pattern and platform-stop policy.
3. The rider may board the first realized eligible revenue train with `OBSERVED_STOP_PRESENCE_AFTER_READY` evidence at or after readiness, with no additional first-leg platform margin in V1.
4. A train is eligible only if its route, direction, boarding platform, downstream platform sequence, schedule relationship, and revenue status satisfy the frozen oracle rules.
5. A realized train scheduled before readiness remains eligible when it is delayed or dwelling and has qualifying observed stop presence after readiness.
6. A train that is already dwelling may be boarded only when a direct `STOPPED_AT` observation at the boarding platform occurs at or after readiness or a direct stop-presence interval proves that it remained observed there.
7. This is a frozen virtual-rider boardability assumption because Vehicle Position data does not expose door state or passenger acceptance.
8. A downstream move timestamp never proves observed stop presence, even when it occurs after readiness.
9. `SKIPPED`, `CANCELED`, non-revenue, short-turn, added, replacement, and unmatched trips follow the frozen decision table in Section 12.5 and matching golden fixtures.
10. For a transfer, the rider becomes ready for the next leg after the destination arrival upper bound of the prior leg plus the direction-specific transfer-walk time.
11. The rider boards the first eligible train with observed stop-presence evidence on the next route-pattern policy after transfer readiness.
12. Destination arrival is represented by the frozen lower and upper bounds for the boarded final train.
13. A deadline is an identified success when the arrival upper bound is at or before it, an identified failure when the arrival lower bound is after it, and interval-unresolved otherwise.
14. A journey is `PROVEN_NO_ARRIVAL_WITHIN_HORIZON` when complete primary-source reconciliation extends through the frozen horizon and the oracle proves that no eligible policy path arrived by that horizon.
15. A journey is `CENSORED` when a required identity or source-observation interval is missing, ambiguous, or incomplete through the horizon.
16. A journey with a valid arrival interval remains `ARRIVED`, while each generated deadline receives its own identified or interval-unresolved status.

This oracle defines the benchmark outcome, not actual passenger behavior.
The model card and UI limitations must say so.
When either Vehicle Position mode is used, identified success means that the train was evidenced at or beyond the destination by the deadline.
That event implies the virtual rider arrived no later than the recorded upper bound, but neither the first stop observation nor the downstream move is the actual alighting timestamp.
The model estimates a latent arrival distribution from these intervals rather than treating observation timestamps as exact arrival events.

Oracle tests must prove that two scheduled departures representing the same route-pattern policy deduplicate to one candidate, while two genuinely different route patterns retain different outcomes.
All primary and baseline policies use the same oracle, static candidate set, and causally available decision-context mask.
Every exceptional-trip and timestamp-boundary golden specifies its complete expected boarding, status, destination-time, and censoring result.

### 13.1 Historical query population

Freeze the query population before resolving outcomes.
The acceptance charter selects one contiguous historical start date and end date from complete-interval source inventory, schema fingerprints, schedule coverage, and the aggregate Milestone 0 scope audit before candidate outcomes are resolved.
Every service date in that interval with scheduled service in the retained aggregate scope remains eligible for query generation.
An individual service date, time window, origin-destination pair, disruption, source outage, ambiguous trip, or incomplete outcome window may not be removed because its outcomes are difficult to resolve.
Those cases remain in the population and become resolved, censored, abstained, or unavailable under the frozen rules.
Milestone 0 may exclude an entire line, station, or transfer station only when its aggregate audit coverage fails a predeclared Section 6.1 threshold, and every public estimand is conditional on that explicitly published aggregate scope.

The V1 protocol is:

- Enumerate every supported origin-destination station pair with a feasible zero-transfer or one-transfer static path.
- Stratify by ordered route pair and transfer station.
- Select up to 12 origin-destination pairs per stratum by ascending keyed hash using the public seed in the acceptance charter, retaining every pair when a stratum contains fewer than 12.
- Require at least 100 distinct base origin-destination pairs after the permitted aggregate line and station scope decision or narrow the public scope and rerun the audit.
- Generate readiness horizons of zero, five, ten, and 15 minutes after query time so the complete model-supported initial ready-lead interval is evaluated without extrapolation beyond its frozen endpoints.
- Generate query times every 30 minutes from 06:00 through 23:00 local service time on every retained service date in the contiguous interval.
- Define deadline variants as every exact five-minute increment from five through 180 minutes after readiness so the historical decision population equals the served deadline domain.
- Give every base tuple of service date, origin-destination pair, query time, and readiness horizon equal total weight.
- Divide that base weight equally across its deadline variants so repeated deadlines cannot dominate training or evaluation.
- The public request-lattice inventory is the Cartesian product of reliability targets 0.80, 0.90, and 0.95 with every integer maximum-extra-time cap from zero through 20 minutes, serialized in numeric target-then-cap order.
- For output-support discovery and calibration, encode each deadline-variant key with the acceptance charter's canonical length-prefixed UTF-8 key schema, compute `HMAC-SHA256(public_query_seed, encoded_key)`, and sort within each chronological split by digest bytes and then encoded-key bytes.
- Assign sorted position `i` to public request-lattice member `i mod 63` from the numeric target-then-cap inventory, which gives every deadline variant exactly one member and makes per-member counts differ by at most one in each split.
- Balancing occurs independently inside every chronological split without outcome access, and the assignment manifest is frozen before any candidate model is trained.
- Each assigned public request variant retains its deadline variant's full weight for support, calibration, slice, and quantile reports, so the assignment neither dilutes nor multiplies total population weight.
- The primary 0.90-target and 20-minute-cap comparison is scored separately on every deadline variant with that deadline variant's full weight and never substitutes only the balanced-assignment subset.
- Only secondary target-and-cap comparisons named in the acceptance charter are scored separately on every deadline variant and enter the frozen Holm family.
- The assigned request key is the base-query identifier, deadline-variant identifier, reliability target, and integer cap, and no assigned variant may be removed or reassigned because its decision is unsupported or its outcome is unresolved.

The manifest records every aggregate excluded line, station, transfer station, and resulting station pair with the frozen audit rule and exact reason.
It also records the number and frozen weight of scheduled queries removed by each aggregate scope exclusion.
Query generation uses only the frozen schedule, keyed sampling rule, and aggregate scope allow-list, never individual realized journey outcomes or per-query source completeness.
Source outages and incomplete observation windows inside the retained date interval remain in end-to-end availability, resolution-rate, and censoring-bound denominators.

## 14. Candidate generation

Use OpenTripPlanner for static-schedule route-pattern discovery and canonical schedule simulation.
Do not rebuild a general transit router.

Candidate-generator modes are explicit registry values:

- `STATIC_ROUTE_POLICY_V1` is mandatory for `historical_v1`, offline evaluation, production scoring, and the first prospective shadow panel.
- `TRIP_UPDATE_SPECIFIC_BASELINE_V1` may score the official Trip Update estimate against the same frozen route-pattern universe, but it remains a separate baseline.
- Any future `REALTIME_ROUTED_V2` mode that lets Trip Updates add, remove, or retime primary candidates is prospective-only and receives no calibration or superiority claim until its own frozen shadow test passes.

The registry rejects a decision-policy bundle whose candidate-generator mode differs from the mode named by its evaluation artifact.
The registry also rejects a decision artifact whose static candidate-manifest hash, decision-context version, alert lineage, or eligibility-mask hash differs from its recorded evaluation inputs.

Build and freeze historical candidate generation before labels, features, or models are produced.

V1 candidate constraints are:

- Subway only.
- Zero or one transfer.
- Named stations.
- An explicit 90-minute departure window.
- At most 16 normalized alternatives after route-pattern deduplication.
- Conservative transfer duration.
- No itinerary requiring unsupported station connectivity.

Normalize every OpenTripPlanner response into the project-owned `CandidateItinerary` contract.
The decision service must not depend directly on unstable external response fields.

Candidate generation must be deterministic for a pinned graph, request, generator mode, and realtime snapshot when that mode permits one.
The graph build records GTFS, OpenStreetMap if used, configuration, Java, container, and OpenTripPlanner hashes.

Candidate-generation manifests also record the agency time zone, request time, arrive-by setting, search window, itinerary cap, traversal configuration, graph build time, and wall-clock override used by the router.
Historical replay must produce identical candidates after a process restart.
Production `historical_v1` parity tests submit the same graph, static request, and clock override as offline replay and require identical normalized policy keys and canonical schedule features.
Decision-context parity tests then apply the same causally available alert revisions and require identical eligibility masks without changing those normalized candidates.

Implement a small audit enumerator that traverses the static station-route graph for every simple zero-transfer and one-transfer route pattern.
It is not a production router.
Its only purpose is to measure whether the bounded OpenTripPlanner request omitted a supported route pattern.
When more than 16 normalized route-pattern policies are available, retain them by scheduled arrival, scheduled departure, transfer count, route-pattern tuple, platform-stop tuple, and then bytewise policy-key order.
The truncation order is outcome-independent, belongs to the candidate-generator version, and is shared by OpenTripPlanner normalization and the audit enumerator.
The frozen recall corpus covers every supported origin-destination pair for every unique service-calendar, timetable, transfer-connectivity, and 90-minute-window equivalence class used by the complete frozen query population at all four readiness horizons.
Four representative clock times may remain a smoke fixture, but they cannot satisfy the final recall gate by themselves.
The denominator is every enumerator route pattern that satisfies the same platform-connectivity, transfer-walk, service-calendar, pickup, drop-off, and 90-minute-window rules as production.
The frozen population corpus must recover the static fastest route for every supported query, meet 99 percent route-pattern recall overall, and meet at least 95 percent recall for every supported line, transfer station, and origin-destination pair with at least 20 eligible policy instances before label generation.

## 15. Feature design

Feature families include:

- Scheduled leg and itinerary duration.
- Scheduled transfer buffer.
- Conservative transfer walk.
- Route, direction, origin, destination, and transfer identifiers.
- Cyclical time-of-day and day-of-week representation.
- Holiday and planned-service indicators.
- Recent past-observed delay distribution.
- Recent past-observed headway distribution.
- Recent causally resolved cancellation or unmatched-event rate when an online-equivalent derivation exists.
- Vehicle Position age and progress through the same primitive semantics available in historical replay.
- Alert category and affected entity when causally available.
- Feed age, missingness, and degraded-mode indicators.

Deadline slack is query and support-policy metadata used only for deadline-variant generation, the static horizon predicate, CDF evaluation, and decision selection.
It never enters `FeatureRow`, the arrival AFT model, the transfer classifier, or a fitted preprocessing transform.

Do not use:

- Future realized headways.
- Future alerts.
- Final destination outcome.
- A post-journey trip-match result as an online feature.
- LAMP final stop count, nearest-schedule fallback match, coalesced Trip Update arrival, next-stop-derived departure, dwell, or headway unless it is causally re-derived from allowed primitives.
- GTFS Realtime Trip Update prediction residuals in the `historical_v1` schema.
- Random row-level target encodings that mix future service days.
- Rider identity or personal history.

Every feature has a registry entry containing owner, type, units, source, event-time rule, product-availability rule, online-equivalent derivation, default behavior, and seeded leakage test.

## 16. Baselines

Implement baselines before the candidate model.

Required baselines are:

1. Static schedule treated as deterministic.
2. Latest GTFS Realtime prediction treated as deterministic for prospective data.
3. Rolling median delay by route, direction, station, weekday, and time bucket.
4. Empirical time-of-week quantiles.
5. Regularized logistic deadline-CDF classifier with a monotonic deadline-slack coefficient.
6. Point-estimate gradient-boosted arrival model converted to an empirical residual distribution using training data only.
7. Fastest candidate from OpenTripPlanner.

Every baseline uses the same candidate set, temporal view, evaluation queries, and outcome resolver where applicable.
Threshold-classification baselines train only on deadline outcomes identified by the arrival interval and use the same frozen base-query weights.

## 17. Core modeling plan

### 17.1 Coherent interval-censored arrival-time CDF

Train one candidate-level accelerated-failure-time model for the latent duration from virtual-rider readiness to destination arrival.
The mandatory initial implementation uses an exact pinned XGBoost `survival:aft` objective or another audited implementation that accepts per-row lower and upper label bounds and evaluates the interval-censored likelihood directly.
Deadline is not a model feature.
At inference, deadline success is the fitted arrival-duration CDF evaluated at `deadline_utc - ready_at_utc`, which makes deadline monotonicity structural rather than dependent on duplicated threshold rows.
For the pinned XGBoost path, inference requests the raw AFT margin `m(x)` rather than interpreting the default predicted event-time label as a probability.
For positive duration `t`, the uncalibrated CDF is computed as `F_Z((log(t) - m(x)) / sigma)`, where `F_Z`, `sigma`, and the raw-margin convention come from the exact pinned XGBoost distribution family, scale, and version.
The uncalibrated probability is exactly zero for nonpositive duration, and numerical clipping may not replace that semantic boundary.
Golden tests must compare the hand-computed normal, logistic, and extreme-value CDF values with the pinned library's raw margins and AFT likelihood fixtures before any model bundle can be registered.

For each `ARRIVED` candidate, the training lower and upper labels are the destination arrival interval converted to seconds after readiness.
For each `PROVEN_NO_ARRIVAL_WITHIN_HORIZON` candidate, the lower label is the observation horizon and the upper label is positive infinity, producing a right-censored observation.
A `CENSORED` candidate may contribute a right-censored prefix only when reconciliation independently proves no arrival through `observation_complete_through_utc`.
Otherwise it is excluded from the likelihood, preserved in censoring reports and policy bounds, and never converted to a failure.
Every included AFT row must satisfy `0 < lower_bound <= upper_bound`, where an infinite upper bound is allowed only for a right-censored row.
An invalid or nonpositive bound is quarantined with a reason and may not be repaired by silent clipping or epsilon addition.
If a base query has frozen weight `w` and `K` distinct candidate policies, each included or reported AFT candidate row receives weight `w / K` before any class of censoring is excluded from fitting.
Excluded likelihood rows retain that assigned weight in support and censoring reports, so missing labels cannot redistribute mass to easier candidates.
Repeated generated deadlines do not duplicate an arrival interval or increase its training weight.
The AFT training key is the deadline-independent base-query identifier plus immutable candidate-policy key.
Deadline variants join that one fitted candidate distribution only during decision evaluation.

The AFT distribution family and scale are selected only through the frozen rolling-origin pre-test rule.
The mandatory candidates are normal, logistic, and extreme-value AFT distributions with the same feature set and training weights.
The fitted distribution is evaluated on the frozen `cdf_grid_v1`, which contains five-minute thresholds from readiness through the 210-minute observation horizon plus every exact user deadline represented by the query manifest.
The grid is an evaluation and serialization surface rather than a source of fabricated exact labels.
The registry fails a CDF whose raw formula or calibrated mapping violates monotonicity by more than the frozen floating-point tolerance at any grid point.
A cumulative-maximum guard may correct only a rounding-level reversal no larger than `1e-12`, records the maximum correction, and may not repair a modeling or calibration defect.
Add an exact zero-probability boundary before the physically feasible travel time.
Do not display or validate a quantile beyond the frozen observation horizon.

Before model fitting, the acceptance charter partitions the accepted five-through-180-minute deadline slack into named nonoverlapping half-open 30-minute cells with the final cell closed at 180 minutes.
Every such deadline-slack cell exposed by the UI must contain at least 1,000 distinct noncensored candidate outcomes from at least 500 distinct base queries spanning at least 30 service days in pre-test data.
An unsupported region returns `INSUFFICIENT_EVIDENCE`, and its quantiles are not displayed.
The initial-query lead-time support domain for `historical_v1` is exactly zero through 15 minutes, with the frozen zero, five, ten, and 15-minute historical horizons reported separately in model-validation and final-test artifacts.
The model scorer rejects a feature row outside that lead-time domain, and the service applies the static-schedule fallback in Section 12.4.1 instead of extrapolating.

The calibrated interval-censored distribution evaluated on the grid is the single product latent arrival-time CDF.
Deadline probability is read from that CDF.
The 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, and 0.95 arrival quantiles are obtained by deterministic root finding on the same continuous calibrated CDF rather than by selecting a coarse grid bucket.
Root finding uses a frozen one-second time tolerance and verifies that the returned lower and upper neighboring timestamps bracket the requested probability.
A quantile beyond the observation horizon is reported as unresolved within the horizon rather than fabricated.

Required model invariants are:

- A later deadline never has a lower estimated success probability for otherwise identical inputs.
- Probabilities stay within zero and one.
- Quantiles remain ordered.
- The displayed probability and displayed quantile answer the same CDF.
- Scoring the same bundle and feature row is bitwise stable within the documented numeric tolerance.

### 17.2 Transfer outcome

Train a separate calibrated transfer-success classifier only for transfer-risk explanation and recovery triggering.
It does not generate or override the final deadline probability.
Its probability is explicitly conditional on the trip session or historical virtual rider having reached `AT_TRANSFER`, and it estimates boarding an eligible second-leg train within the frozen window.
It is not an unconditional probability of completing the original first leg or reaching the transfer station.
The initial journey-search response therefore shows scheduled transfer buffer and backup information but keeps `transfer_probability` null until the trip reaches confirmed `AT_TRANSFER`.
The V1 transfer-success window begins at realized transfer readiness and ends exactly 15 minutes later, independently of the journey's 210-minute deadline-outcome horizon.
Historical extraction retains the additional transfer follow-up interval through `transfer_ready_at_utc + 900 seconds` for any candidate that reaches `AT_TRANSFER` no later than the journey horizon.
That additional interval may resolve only the conditional transfer outcome and never extends, relabels, or fabricates the candidate's deadline-arrival outcome.
The separately frozen transfer-reconciliation set in Section 12.5 is a safe superset for that full window, including a second-leg trip scheduled after the journey horizon but no later than the transfer-follow-up bound.
The model card defines transfer success as satisfying the frozen virtual-rider `OBSERVED_STOP_PRESENCE_AFTER_READY` rule for the first eligible second-leg train within that interval.
`transfer_success_window_seconds` is therefore 900 for every V1 transfer candidate, and changing it creates a new outcome and model schema.
When transfer readiness equals a departure-bound timestamp, success requires a `STOPPED_AT` observation at or after readiness or a stop-presence interval whose upper endpoint is strictly after readiness.
A downstream move timestamp at the same instant never proves transfer boarding.
A positive transfer label requires qualifying observed stop presence no later than the window end.
A negative transfer label requires complete per-train reconciliation proving no qualifying eligible train through the exact 900-second window end.
If source completeness does not extend through that exact end, the transfer row is censored regardless of how much of the window was observed and can never become an early failure.
Every ambiguous or incomplete transfer window is censored, excluded from classifier loss, and retained with its frozen weight in coverage and complete-population bounds.

The mandatory transfer-model candidates are a regularized logistic classifier and a histogram gradient-boosted classifier using the same point-in-time feature registry, missingness rules, and chronological boundaries as `historical_v1`.
The historical transfer feature cutoff is the virtual-rider `AT_TRANSFER` transition time, and the production cutoff is the server-recorded accepted `AT_TRANSFER` transaction time.
The conditional transfer model's support domain requires that cutoff to be no later than `initial_effective_ready_at + 210 minutes`.
An accepted live `AT_TRANSFER` state after that boundary remains a valid trip-state transition but returns `transfer_probability = null`, disables `LOW_TRANSFER_PROBABILITY`, and reports `transfer_support_status = UNSUPPORTED_TRANSFER_READY_HORIZON`.
Only information product-available by that cutoff may enter the transfer prediction.
No later Vehicle Position, later alert revision, future second-leg event, or retrospectively corrected first-leg fact may enter it.
The training key is the deadline-independent base-query identifier plus immutable transfer-candidate policy key.
If a base query has frozen weight `w` and `K_transfer` distinct transfer candidates, every included or reported transfer row receives weight `w / K_transfer` before outcome resolution is considered.
Generated deadline variants never duplicate or reweight a canonical classifier or candidate-population transfer row.
Selected-policy trigger occurrences are a separate decision-evaluation table keyed by deadline variant and canonical transfer-row identifier under the weighting rule in Section 6.1.
Only `SUCCESS_IDENTIFIED` and `FAILURE_IDENTIFIED` rows enter classifier or calibrator loss, while censored rows retain their assigned weight in support, resolution, and complete-population calibration reports.
Candidate journeys that never reach `AT_TRANSFER` are outside this conditional transfer-probability estimand, remain in deadline-policy and reachability reports, and may not be relabeled as transfer failures.
Reports publish the frozen weighted mass that reaches `AT_TRANSFER` separately so the conditional metric cannot be mistaken for end-to-end journey reliability.

Select the classifier family and its hyperparameters through the same nested rolling-origin model-validation protocol used for the arrival model before the dedicated calibration-fit window opens.
The predeclared selector first rejects a candidate that misses resolution, feature-support, discrimination, latency, or slice-stability rules in the acceptance charter, then minimizes frozen-weighted log loss, then Brier score, then parameter count, and finally uses the lexical model identifier.
Fit one strictly increasing sigmoid calibrator on the dedicated chronological calibration-fit window using resolved transfer rows and their frozen weights.
An isotonic challenger may replace it only when the predeclared nested pre-calibration comparison improves both frozen-weighted log loss and Brier score and passes every monotonicity, minimum-support, complete-population calibration, and slice rule.
The calibrator family is frozen before the dedicated calibration window opens, is fit once on that window, and is never selected or refit from final-test outcomes.
The registry stores the classifier, calibrator, training-row manifest, calibration-row manifest, row-weight rule, transfer-outcome schema, support policy, and complete-population calibration report separately from the arrival-CDF artifacts.

Final-test and prospective transfer calibration use the candidate-population and selected-policy fixed deciles plus unresolved-outcome bounds in Section 6.1.
Every eligible scored transfer candidate enters its candidate-population decile at decision time, and the confirmatory policy's selected transfer itinerary also enters its selected-policy decile before outcome resolution, including a later censored or missing window.
A numeric probability requires the applicable candidate-population decile and transfer-station gates to pass, and the probability-below-0.50 recovery trigger additionally requires its selected-policy decile gate.
When an applicable gate fails, the API returns `transfer_probability = null` or `transfer_recovery_trigger_status = INSUFFICIENT_EVIDENCE` as appropriate and uses explanation codes based only on supported deterministic facts.
The 0.50 trigger is a frozen product threshold rather than a selected significance cutoff, and recovery benefit remains a secondary Holm-corrected decision hypothesis.

### 17.3 Calibration

Use a dedicated chronological calibration-fit window after model selection and before the final test.
The default calibrator applies one shared `sigmoid(a * logit(p) + b)` mapping to the uncalibrated CDF at every threshold, constrains `a` to be strictly positive, and restores exact zero and one endpoints after numerically stable evaluation.
Any fitted calibrator that violates CDF ordering, bounds, or endpoint tests is rejected rather than repaired after final-test access.
Calibration-fit rows are generated only at frozen thresholds whose outcomes are identified by the arrival interval.
If the calibration manifest contains `T` frozen thresholds, each candidate-threshold cell has weight `w / (K * T)` regardless of outcome resolution.
Only an identified cell enters calibrator fitting, while unresolved cells retain that weight in support and unresolved-outcome reports and never redistribute it to easier cells.
Interval-unresolved thresholds remain in calibration coverage reports and complete-band bounds but never receive a fabricated binary calibration label.

An isotonic challenger may replace it only if a nested rolling-origin comparison on pre-calibration data was predeclared and improves Brier score and log loss without violating minimum support or worsening the complete-band initial selected-policy calibration bound.
After the calibrator family is frozen, fit it once on the dedicated calibration window.
Do not choose or refit it from final-test outcomes.

Use time-ordered conformal adjustment for empirical interval coverage only when its complete validation protocol was frozen before the final test.
Do not claim exchangeability guarantees under operational drift.

### 17.4 Candidate dependence

Do not multiply independent leg probabilities.
The final latent-arrival CDF is trained directly on the complete candidate-policy arrival interval or right-censored outcome and therefore includes missed-transfer and next-eligible-train behavior.

Alternative candidates for the same query remain correlated.
Preserve that dependence in evaluation by comparing policies on identical query identifiers and resampling complete service-day blocks.
No residual simulator is required for V1.

### 17.5 Historical and prospective schemas

The initial deployable bundle is `historical_v1`.
It contains only online-equivalent features with non-missing historical training support.

`prospective_v2` is a separate future bundle that may add Trip Update prediction features only after captured snapshots and matured outcomes support chronological training, validation, calibration, and untouched test windows.
The registry fails a bundle when a deployed feature has no non-missing training support or when the online and offline feature implementations fail parity fixtures.

### 17.6 Model selection

The production candidate is selected on locked pre-test windows by the acceptance charter's decision-aware rubric:

- Probability calibration.
- Deadline success at the selected reliability targets.
- Added planned time.
- Tail lateness.
- Prediction and scoring latency.
- Stability across lines, stations, and disruption slices.

A complex model must pass the numerical improvement, calibration, added-time, support, and latency gates in Section 6.1 or it is rejected.
If no learned model passes but a frozen empirical or schedule-only distribution and its complete decision policy pass every primary calibration, censoring-bound, added-time, support, and latency gate, promote that model-free bundle and label it explicitly.
If neither a learned nor model-free bundle passes, trigger the historical-explorer pivot rather than claiming a successful reliability recommendation model.

## 18. Decision policy

For each query, score the same bounded candidate set.
Construct the `DecisionContext` at the query cutoff, preserve the complete static candidate manifest, and score only entries marked `ELIGIBLE` by its alert-derived mask.
Arrive90, the static-fastest comparator, every secondary policy, historical replay, and live scoring use the identical eligibility mask for a given comparison.
A request whose `effective_ready_at` is more than 15 minutes after the server-owned initial cutoff bypasses model scoring and returns the static-fastest schedule candidate under the frozen `DEGRADED_SCHEDULE_ONLY` branch.
That branch emits no numeric model probability, arrival quantile, target-met claim, trip-start decision, or learned live-feature claim.

Before support lookup or ranking, calibrated deadline probabilities are rounded to six decimal places with decimal round-half-to-even under the frozen decision implementation.
That rounded value is the canonical deadline prediction-band lookup and policy-comparison value, while the unrounded calibrated value is used only for calibration means and diagnostics.
The API may display a coarser percentage, but policy comparisons use that six-decimal value and retain the unrounded model value for diagnostics.
For every eligible candidate, planned time is `scheduled_arrival_utc - ready_at_utc` from the canonical schedule simulation.
The static-fastest comparator is the eligible candidate with the lexicographically smallest tuple of scheduled arrival time, planned time, scheduled departure time, transfer count, ordered route-pattern tuple, ordered platform-stop tuple, and bytewise immutable policy key.
The fastest comparator and its planned time are recomputed from the shared `DecisionContext` mask, so a causally known closure may change the comparator for every policy in that comparison without mutating the static candidate manifest.
If the shared mask contains no eligible candidate, every policy returns `NO_SUPPORTED_ITINERARY` and no extra-time reference is fabricated.

Extra planned time is `candidate_planned_time - comparator_planned_time` and is never negative because the comparator has the earliest canonical scheduled arrival under a common ready time.
The cap-eligible set contains the comparator and every other eligible candidate whose extra planned time is no greater than the request's maximum-extra-time cap.
Candidates outside that set are removed before either target-met or highest-probability selection.
The V1 default cap is 20 minutes, and the user may lower it.
Changing the default requires a new decision-policy version and validation before a later release.

Each cap-eligible candidate receives frozen support results for its own predicted band, every traversed line, its origin, destination, and transfer parent stations, every independently displayed quantile, and any reliability-target cell declared for the request target.
`static_initial_horizon_support(query, support_manifest)` is true if and only if the effective ready lead is in the closed interval from zero through 15 minutes, the feature cutoff is no later than effective readiness, `effective_deadline_at` is an exact five-minute increment from five through 180 minutes after effective readiness, and the query's frozen 30-minute deadline-slack region passed the pre-fit candidate-support rule in Section 17.1.
An absent or unknown deadline-slack region in the support manifest makes `static_initial_horizon_support` false.
`declared_target_support(target, eligibility_manifest)` is true when no target-specific cell is declared for `target`, and otherwise is true if and only if every target-specific cell declared for `target` is eligible in the same iteration-input eligibility manifest.
An absent or unknown cell declared for the target makes `declared_target_support` false.
`requested_target_support(candidate, target, query, eligibility_manifest, support_manifest)` is true if and only if `static_initial_horizon_support(query, support_manifest)` and `declared_target_support(target, eligibility_manifest)` are true and the candidate's own predicted-band cell and every applicable line and parent-station deadline-slice cell are eligible in the same iteration-input eligibility manifest.
An absent applicable cell, an unknown cell identifier, or any ineligible member makes the requested-target conjunction and the fallback-support predicate fail closed, while quantile eligibility is independent and never participates in deadline recommendation selection.
The target-qualified set contains only cap-eligible candidates whose rounded calibrated deadline probability is at least the requested target and whose requested-target support result passes.
If the target-qualified set is nonempty, Arrive90 selects the lexicographically smallest tuple of scheduled arrival time, negative rounded probability, planned time, transfer count, ordered route-pattern tuple, ordered platform-stop tuple, and bytewise immutable policy key from that set.
This rule makes the recommendation the earliest scheduled arrival that meets the target and uses probability only to break equal-arrival choices.
If the target-qualified set is empty, the fallback-supported set contains every cap-eligible candidate for which `static_initial_horizon_support(query, support_manifest)` is true and whose own predicted-band cell and every applicable line and parent-station deadline-slice cell pass in the same iteration-input eligibility manifest.
This rule applies equally to the fixed bands below 0.80, so a low-probability fallback is numeric only when its exact band, every applicable slice, and the static initial ready-horizon rule pass the frozen gates.
When that set is nonempty, Arrive90 selects its lexicographically smallest tuple of negative rounded probability, scheduled arrival time, planned time, transfer count, ordered route-pattern tuple, ordered platform-stop tuple, and bytewise immutable policy key.
That fallback returns `TARGET_NOT_MET` only when `declared_target_support(target, eligibility_manifest)` is true and every cap-eligible candidate with probability at least the requested target has a passing requested-target support result, and it otherwise returns `INSUFFICIENT_EVIDENCE` because the requested target or a candidate that could have met it is unsupported.
When the fallback-supported set is empty, Arrive90 returns the static-fastest comparator as the schedule-only recommendation with `INSUFFICIENT_EVIDENCE` and suppresses unsupported model probabilities.
The response always returns the static-fastest comparator separately, even when it is also the recommendation.

For a selected transfer itinerary, `backup_departure` is the earliest later same-service-day revenue trip on the selected second-leg route pattern that serves the exact boarding and alighting platforms in order after its canonical second-leg trip and before the observation horizon.
Backup-departure ties use scheduled departure, scheduled arrival, stop-sequence tuple, and bytewise `trip_id` order.
When the primary recommendation is `TARGET_MET`, the optional `backup_itinerary` is the remaining target-qualified candidate with a different immutable policy key and the smallest target-met selection tuple.
When the primary recommendation is `TARGET_NOT_MET` or model-based `INSUFFICIENT_EVIDENCE`, it is the remaining fallback-supported candidate with a different immutable policy key and the smallest highest-probability selection tuple.
When the recommendation has fallen back to static schedule because the fallback-supported set is empty, `backup_itinerary` is null.
The response distinguishes `backup_departure` from `backup_itinerary`, returns null when either does not exist, and never describes a duplicate departure response as a different policy.

The decision-policy manifest freezes the probability rounding mode, initial comparator tuple, planned-time and extra-time formulas, cap-filter order, complete pre-test output-support eligibility manifest and fixed-point discovery-artifact hash, exact requested-target support conjunction, fallback-support filters, status rules, target-met tuple, target-not-met tuple, transfer-count convention, policy-key byte order, both initial backup rules, recovery-reason precedence, recovery candidate universe, recovery cap-reference rule, continuation-comparator rule, deterministic recovery ranking and backup tuples, recovery model-output suppression rule, and trip-state transition graph.
Historical replay, the API, shadow evaluation, and causal replay must produce byte-identical selections from the same scores, candidate manifest, and decision context.

Unsupported 0.95 requests follow the same frozen fallback-supported ranking and return `INSUFFICIENT_EVIDENCE` rather than extrapolating confidence.

The response distinguishes:

- `TARGET_MET`.
- `TARGET_NOT_MET`.
- `DEGRADED_SCHEDULE_ONLY`.
- `STALE_LIVE_DATA`.
- `MODEL_ABSTAINED`.
- `INSUFFICIENT_EVIDENCE`.
- `NO_SUPPORTED_ITINERARY`.

Calibration, target-violation, and deadline-success metrics are always recomputed on the candidate selected by this complete policy.
Candidate-row calibration alone cannot satisfy a decision gate.

### 18.1 Live trip update contract

The `historical_v1` arrival CDF is supported only for an initial query whose feature cutoff is no later than readiness and whose ready lead is within the frozen zero-through-15-minute domain.
The initial deadline probability and arrival quantiles are immutable attributes of the initial decision snapshot and remain labeled with their original cutoff, model bundle, feature schema, and decision context throughout the trip.
No post-start Vehicle Position, Trip Update, alert, or clock advance may cause that initial CDF to be scored again with a feature cutoff after its original readiness time.

Every post-start update transaction captures a server-owned `trip_update_cutoff_utc` before reading a feed, alert, schedule, model, or trip-state record.
Only source records product-available and fetched no later than that cutoff may enter the update.
V1 SSE events after trip creation are limited to deterministic feed-freshness changes, official Trip Update annotations that remain visibly separate from Arrive90 estimates, causally known alert eligibility or closure changes, state-transition acknowledgments, the independently supported conditional transfer estimate after `AT_TRANSFER`, and a new recovery decision under Section 18.2.
An official Trip Update annotation is emitted only when its trip identity is unambiguously associated with the confirmed trip state or the separately named specific-trip baseline, and it remains null when that association is absent or ambiguous.
A deterministic alert update may mark the selected policy `ORIGINAL_POLICY_UNSUPPORTED`, but it never reranks candidates with frozen initial scores or emits a revised deadline probability or arrival quantile.
When an unstarted rider wants a refreshed deadline estimate, the product stops the old trip session and performs a new initial search with a new decision capability.
When an active rider's selected policy becomes unsupported, the product prompts the confirmed-state recovery flow or displays conditional guidance until state is confirmed.
Every SSE event identifies its event kind, server cutoff, source-attempt lineage, freshness state, and whether each displayed value is the frozen initial Arrive90 estimate, an official deterministic annotation, a conditional transfer estimate, or a new recovery decision.
The UI keeps a frozen initial estimate visibly labeled `AS_OF_INITIAL_DECISION` and never presents an official Trip Update value or deterministic closure event as a recalibrated Arrive90 probability.

A future mid-journey deadline rescorer requires a separately versioned state-conditioned remaining-arrival outcome, feature cutoff, candidate policy, model, support domain, calibration protocol, final-test evaluation, and registry bundle before it may emit a revised numeric deadline probability or quantile.
Ordinary SSE transport, collector, or UI work cannot enable that future capability by configuration alone.

### 18.2 Recovery state machine

Recovery recommendations are conditional on an explicit trip-session state rather than inferred user location.

The V1 states are:

- `NOT_STARTED`.
- `ON_FIRST_LEG`, confirmed by the user with the boarded trip or route-pattern identifier.
- `AT_TRANSFER`, confirmed by the user or reached by the deterministic virtual-rider replay.
- `ON_FINAL_LEG`, confirmed by the user.
- `ENDED`.

State names are relative to the currently active server-issued itinerary, so `ON_FIRST_LEG` may describe the first leg of an activated one-transfer recovery itinerary.
Trip creation begins in `NOT_STARTED`.
For a zero-transfer active itinerary, the only boarding transition is `NOT_STARTED -> ON_FINAL_LEG`.
For a one-transfer active itinerary, the ordinary transitions are `NOT_STARTED -> ON_FIRST_LEG -> AT_TRANSFER -> ON_FINAL_LEG`.
At `AT_TRANSFER`, atomically activating and boarding a server-issued zero-transfer recovery itinerary permits `AT_TRANSFER -> ON_FINAL_LEG`.
At `AT_TRANSFER`, atomically activating and boarding the first leg of a server-issued one-transfer recovery itinerary permits `AT_TRANSFER -> ON_FIRST_LEG`, after which that active itinerary follows `ON_FIRST_LEG -> AT_TRANSFER -> ON_FINAL_LEG`.
The transition request that activates a recovery itinerary must reference an unexpired, unconsumed recovery decision issued for the same trip, current state version, and current station.
`POST /v1/trips/{trip_id}/stop` is the only client-requested path from any nonterminal state to `ENDED`, while server expiry deletes the session instead of fabricating a user transition.
`ENDED` has no outgoing edge, a new same-state transition is invalid, and no rollback to an earlier state is allowed.
An idempotent replay of an already committed edge returns its original response without creating another edge.

The public `trip_id` is a nonsecret random UUID and never acts as authorization.
Every trip-startable journey-search response includes a server-owned `decision_id` bearer capability with 256 bits of cryptographic randomness that expires after ten minutes and resolves to the exact request, candidate manifest, decision context, model bundle, eligibility mask, and returned recommended-itinerary identifier.
The server returns the plaintext decision capability only in that `Cache-Control: no-store` response and stores only an HMAC-SHA256 digest keyed by a versioned decision-capability secret.
The browser retains the plaintext decision capability in memory only and never places it in a URL, cookie, local storage, session storage, browser history, analytics event, error report, or log.
Decision-capability verification derives the keyed digest, uses constant-time comparison for any candidate digest, and retains an old decision key only until every capability issued under it has reached the fixed ten-minute expiry.
The unsupported future-ready schedule-only branch is informational, returns no `decision_id`, and cannot create a trip session.
`POST /v1/trips` atomically consumes that single-use `decision_id` and accepts only the exact `recommended_itinerary` identifier in its server-owned decision record.
The fastest comparator, backup itinerary, and alternatives are informational and cannot start a trip session under that decision capability.
The transaction validates the capability, expiry, and exact recommended identifier before its conditional consume, so an identifier mismatch creates no session and leaves an otherwise valid capability unconsumed.
An expired or consumed decision record is deleted, and concurrent creation attempts can produce at most one trip session.
The server never accepts client-supplied model, feature, candidate-manifest, eligibility, or route-pattern metadata as authoritative trip state.
`POST /v1/trips` creates a separate 256-bit bearer secret, returns it exactly once over an authorized transport, and stores only an HMAC-SHA256 digest keyed by a versioned server secret.
Bearer verification uses a constant-time digest comparison, and server-secret rotation keeps an explicit bounded verification window for sessions created under the previous key version.
The browser retains the bearer secret in memory only and never places it in a URL, cookie, local storage, session storage, analytics event, or log.
Every trip read, state mutation, SSE subscription, and stop request requires `Authorization: Bearer` with that trip's secret.
The frontend consumes SSE through an authenticated `fetch` stream because the native `EventSource` API cannot set the required authorization header.
Refreshing or closing the page loses the V1 secret and requires the user to start a new trip session.
The API retains only the public trip identifier, bearer digest, immutable initial decision snapshot, selected itinerary, explicit state, state-update timestamps, creation time, and expiry time.
The initial decision snapshot contains the requested and effective deadlines, deadline normalization status, reliability target, maximum-extra-time cap, initial cutoff, model and feature versions, static candidate-manifest hash, initial decision-context and eligibility-mask hashes, selected and backup itinerary identifiers, and frozen initial probabilities and quantiles needed for attribution.
The original target and cap determine whether the supported probability trigger is authorized, but the frozen initial deadline probability and quantiles never score or rank a recovery action.
It stores no coordinates or rider identity and deletes the session, digest, SSE history, and state no later than six hours after creation.

A recovery decision is a new point-in-time schedule query whose cutoff is the server's transaction-commit timestamp for the accepted recovery-trigger or state-transition event, origin is the confirmed current or transfer station, ready time equals that same server timestamp in V1, and the effective initial deadline remains the evaluation deadline only for rider context and offline outcome evaluation.
Client-provided clocks and timestamps are never authoritative for recovery cutoff, readiness, feature access, transition order, or outcome evaluation.
The trigger, candidate generator, transfer-model bundle used only by an eligible probability trigger, deterministic recovery policy, and state assumption are versioned.
Historical recovery evaluation uses the virtual-rider state and compares the selected recovery policy with continuing on the next eligible train.
If the user has not confirmed state, the interface may show only conditional language such as `If you are on this train` and may not claim to know the rider's location.

V1 actionable recovery begins only in `AT_TRANSFER` after the rider confirms the transfer station or the historical oracle reaches it.
The frozen probability trigger fires only when the active itinerary is still the exact original recommendation selected by the confirmatory 0.90-target and 20-minute-cap policy, no recovery itinerary has ever been activated, the six-decimal rounded calibrated transfer-success estimate is below 0.50, and the candidate-population decile, transfer-station, and selected-policy decile gates all pass.
Activating any recovery itinerary permanently disables `LOW_TRANSFER_PROBABILITY` for that trip session because a recovery-selected transfer is outside the frozen selected-policy trigger population.
An independently supported conditional transfer estimate may still be displayed on a later recovery-selected transfer under its candidate-population decile and transfer-station gates, but it cannot authorize recovery.
Trips initiated under another target or cap label the probability-based trigger unsupported and may still receive closure-triggered recovery search.
A causally available closure that makes the next uncompleted leg of the current active itinerary unsupported triggers recovery independently of transfer-model support.
When both V1 reasons apply at one cutoff, the frozen precedence is `CAUSAL_CLOSURE` and then `LOW_TRANSFER_PROBABILITY`.
Every recovery decision records that winning reason and the complete set of simultaneously applicable reasons.
Each actionable recovery response carries a nonsecret random `recovery_decision_id` that is visible only on the bearer-authorized trip stream, expires after ten minutes, and resolves server-side to the trip identifier, expected state version, current station, decision context, candidate manifest, cap reference, continuation comparator, and returned selectable itinerary identifiers.
For each recovery decision, `original_continuation` means the next uncompleted continuation policy of the current active itinerary at that decision's cutoff, even when that active itinerary came from an earlier recovery.
The recovery candidate universe contains that decision-local original continuation plus every new zero-transfer or one-transfer policy generated from the confirmed station under `STATIC_ROUTE_POLICY_V1`.
The recovery `DecisionContext` applies the causally available alert mask to that immutable universe, retains the decision-local original continuation for comparator and cap-reference purposes, and never treats that continuation as a selectable recovery action.
For `LOW_TRANSFER_PROBABILITY`, the cap reference is the original continuation when it remains eligible.
For `CAUSAL_CLOSURE`, or whenever the original continuation is otherwise ineligible, the cap reference is the static-fastest eligible recovery candidate under the exact comparator tuple in Section 18.
If no eligible distinct recovery candidate exists, the response uses `recovery_status = NO_DISTINCT_RECOVERY_ACTION`, contains the original continuation and reason codes for audit, and issues no `recovery_decision_id`.
Recovery planned time is measured from the recovery ready time to canonical scheduled arrival, and recovery extra planned time is candidate planned time minus cap-reference planned time.
A distinct recovery candidate may have negative extra planned time when an eligible continuation is the cap reference, and the cap admits every eligible distinct recovery alternative whose extra planned time is no greater than 20 minutes.
Recovery selection never invokes the arrival-time CDF, consumes a deadline probability or quantile, applies the initial deadline-support manifest, or emits `TARGET_MET`, `TARGET_NOT_MET`, or any other reliability-target claim.
A supported transfer probability may authorize `LOW_TRANSFER_PROBABILITY` under the candidate-population decile, transfer-station, and selected-policy decile gates, but that transfer value never scores or ranks a recovery candidate.
The recovery selectable set contains every alert-eligible candidate with an immutable policy key distinct from the original continuation and within the frozen cap, and it is derived only from the static candidate universe, the current causal alert mask, the cap reference, and canonical schedule fields.
When that set is nonempty, the recovery recommendation is its lexicographically smallest tuple of scheduled arrival time, planned time, scheduled departure time, transfer count, ordered route-pattern tuple, ordered platform-stop tuple, and bytewise immutable policy key.
The optional recovery backup is the remaining selectable candidate with a different immutable policy key and the smallest same tuple, or null when no such candidate exists.
The recovery response uses `recovery_status = RECOVERY_ACTION_AVAILABLE`, sets every new deadline probability and arrival quantile to null, and omits reliability-target status.
The cap reference is returned separately from the recommendation, and the original continuation is returned separately as `continuation_comparator` even when it is masked and not selectable.
Only a selectable itinerary returned by that recovery decision can replace the active itinerary through the legal transition graph above.
The recovery evaluation population is every frozen final-test transfer journey whose original recommended itinerary reaches its first `AT_TRANSFER` state with primary evidence and fires either `CAUSAL_CLOSURE` or the fully supported `LOW_TRANSFER_PROBABILITY` trigger from only point-in-time evidence available at the recovery cutoff.
V1 has no manual recovery trigger, so no hypothetical user-request event enters the recovery evaluation or product claim.
Repeated closure-triggered recovery after a recovery itinerary is active remains a deterministic continuity path but is excluded from the V1 recovery-benefit estimand and reported descriptively.
Its outcome is deadline success under the same virtual-rider oracle, its comparator is the recorded original continuation policy regardless of cap-reference eligibility, and its difference uses the same full-population censoring bounds as the core policy.
When a closure masks continuation, the comparator's realized outcome is still resolved or censored through the oracle and complete-observation rules rather than being assigned failure merely from the mask.
Recovery is a secondary Holm-corrected hypothesis and cannot satisfy the core Milestone 6 improvement gate.
Any earlier `ON_FIRST_LEG` recovery display remains explicitly conditional and is excluded from the V1 recovery claim.

Every state mutation includes a random idempotency key, the expected monotonic `state_version`, the requested next state, and any claimed boarded itinerary or route-pattern identifier.
The server accepts only the exact edges above and only identifiers belonging to the active itinerary or an unexpired, unconsumed server-issued recovery decision for that trip and state version.
An idempotent retry returns the original result, while a stale state version returns a conflict without applying the transition.
The persisted state update and emitted SSE event commit atomically through an outbox or equivalent transaction so concurrent requests cannot reorder rider state.

## 19. Deterministic explanations

Explanations are rule-based summaries of observable features and model outputs.
No LLM is used.

Example explanation codes include:

- `SHORT_TRANSFER_BUFFER`.
- `LONG_BACKUP_WAIT`.
- `HEADWAY_VARIABILITY_ELEVATED`.
- `CURRENT_DELAY_ELEVATED`.
- `ACTIVE_SERVICE_ALERT`.
- `LIVE_FEED_STALE`.
- `HISTORICAL_SUPPORT_SPARSE`.
- `EXTRA_TIME_FOR_RELIABILITY`.

Every code maps to a documented sentence template and the data used to trigger it.
Explanations must not imply causal feature importance unless a causal analysis exists.

## 20. Model registry and reproducibility

Each model bundle contains:

- Model binary.
- Preprocessor.
- Arrival-CDF calibrator.
- Calibrator-fit row manifest, interval-label rule, threshold rule, and exact base-query and row weights.
- Transfer classifier and transfer calibrator when enabled.
- Transfer training and calibration row manifests, outcome rule, canonical candidate-weight rule, selected-policy trigger-occurrence manifest and weight rule, fixed-decile support report, and station-support report.
- Feature schema.
- Model schema identifier such as `historical_v1` or `prospective_v2`.
- Online-offline parity fixture hash.
- Data-manifest hash.
- Training configuration hash.
- Code commit.
- Dependency lock hash.
- Random seeds.
- Chronological split dates.
- Acceptance-charter hash.
- Support-policy manifest.
- Static candidate-generator mode and normalized policy-key schema.
- Decision-context schema, alert-mask rule version, and parity-fixture hash.
- Decision-policy manifest with probability rounding, comparator, cap, selection, backup, recovery, and trip-state rule hashes.
- Complete pre-test output-support eligibility manifest with deadline-band, deadline-slice, target-cell, transfer-cell, trigger-cell, and quantile-level evidence hashes and immutable final-test status.
- Eligibility fixed-point discovery artifact with ordered cell inventory, iteration input and output manifest hashes, initial-selected-population hashes, metric hashes, simultaneous removal sets, final verification hash, and complete artifact hash.
- Evaluation report.
- Model card.
- Compatibility range for the API.

The API loads only bundles that pass schema, hash, and compatibility validation.
Model promotion is an explicit manifest change.
Do not use a mutable `latest` model identifier in a published result.

## 21. Offline split protocol

Use contiguous chronological boundaries.
A provisional split is 60 percent training, 15 percent model validation, 10 percent calibration fit, and 15 percent final test by service date.
Exact dates are fixed only after Milestone 0 determines usable coverage and major discontinuities.
Freeze the exact boundaries before any candidate model is trained.

The protocol must include:

- No random split of trip-stop rows.
- A frozen final test interval.
- A dedicated calibration-fit window.
- Nested rolling-origin folds inside pre-calibration data for any calibrator-family comparison.
- Service-day grouped feature transforms.
- A line or major-transfer-station holdout as supplemental generalization evidence.
- No use of final-test labels for threshold or explanation selection.
- No service date, trip, base query, or generated deadline variant spanning more than one split.

Report slices for:

- Peak and off-peak.
- Weekday and weekend.
- Ordinary and disruption periods.
- Each supported line.
- Each major transfer station.
- Short and long horizons.
- Fresh, stale, and missing live data.
- Exact and ambiguous planned-trip matches.
- Primary Vehicle Position labels and Trip Update fallback sensitivity labels.
- Selected-policy recommendations and nonselected candidates separately.

## 22. Evaluation metrics

### 22.1 Predictive metrics

- Pinball loss by quantile.
- Continuous ranked probability score when supported by the distribution representation.
- Interval coverage.
- Average interval width.
- Brier score.
- Log loss.
- Reliability diagrams.
- Expected calibration error with binning sensitivity disclosed.
- Maximum calibration error.
- MAE as a secondary point metric.

### 22.2 Decision metrics

- Deadline-arrival success rate.
- Missed transfers per 100 virtual journeys.
- Interval-identified mean lateness conditional on finite `ARRIVED` intervals, with the excluded censored mass reported.
- Interval-identified P95 lateness conditional on finite `ARRIVED` intervals, with the excluded censored mass reported.
- Added planned travel time.
- Probability-target violation rate.
- Complete-band initial selected-policy calibration bound and resolved-only diagnostic gap at each requested target.
- Complete-population transfer calibration bounds for candidate deciles, transfer-station expected calibration, and confirmatory selected-policy trigger deciles.
- Recovery-policy success rate.
- Partially identified regret bounds against a hindsight oracle restricted to the frozen candidate set and primary evidence.
- Improvement over fastest-route and schedule-only policies.

Every outcome-dependent decision metric reports its complete-population lower and upper bounds under unresolved outcomes, and any resolved-only or finite-interval-only point estimate is labeled supplementary and reports its retained frozen weight.

The main result is a reliability-time Pareto curve rather than one cherry-picked threshold.
The only confirmatory point on that curve is the 0.90 target with a 20-minute cap against the static fastest-candidate comparator.
Every other point is secondary and receives the frozen Holm correction.

All policy comparisons use the identical frozen base queries, static candidate sets, decision-context eligibility masks, alert lineage, outcome resolver, and applicable frozen weights.
Deadline variants from one base query retain constant total weight, and balanced public-lattice assignment retains each deadline variant's full weight in output-support reports.
A primary or registered secondary policy contrast independently scores its fixed target-and-cap policy on every deadline variant with that same full deadline-variant weight.
A base-query deadline variant is `paired_resolved` only when every policy in its comparison has a deadline label identified as success or failure by its primary arrival interval.
The primary paired estimate uses only `paired_resolved` variants, while every excluded variant remains in the reported resolution rate and best-case and worst-case censoring bounds.

For the confirmatory comparison, let `A_i` and `C_i` be binary deadline-success outcomes for Arrive90 and the comparator, and let `w_i` be the frozen base-query deadline-variant weight.
An arrival interval that straddles the deadline is unresolved for these formulas even when the journey status is `ARRIVED`.
When both deadline outcomes resolve, the contribution to both bounds is `A_i - C_i`.
When only `C_i` resolves, the lower contribution is `-C_i` and the upper contribution is `1 - C_i`.
When only `A_i` resolves, the lower contribution is `A_i - 1` and the upper contribution is `A_i`.
When neither resolves, the lower contribution is negative one and the upper contribution is positive one.
`Delta_lower` and `Delta_upper` are the weighted means of those contributions over every frozen final-test deadline variant, including unavailable system decisions.
The primary estimand is the identified interval `[Delta_lower, Delta_upper]`.
The paired-resolved point estimate is supplementary and may not override that interval.
Complete-service-day block bootstrap replicates recompute both bounds, and the primary improvement gate requires the 95 percent lower confidence limit for `Delta_lower` to exceed zero.

Initial selected-policy calibration uses the complete prediction-band population defined in Section 6.1.
Each bootstrap replicate preserves every initial selected-policy decision, its prediction, its resolution status, and its frozen weight, then recomputes `predicted_mean`, `success_lower`, `success_upper`, and the worst-case absolute calibration bound.
A resolved-only reliability diagram is supplementary and may never satisfy a calibration gate.
Because `historical_v1` excludes fully censored journeys from primary threshold loss, an unconditional full-population calibration claim is permitted only when the corresponding complete-band worst-case gate passes.
For a predicted quantile time `q_tau` and an observed arrival interval `[L, U]`, one-sided quantile coverage contributes `1[U <= q_tau]` to the lower bound and `1[L <= q_tau]` to the upper bound, with `U = +infinity` supported for right censoring.
An outcome with no defensible finite lower bound contributes zero to the lower coverage bound and one to the upper coverage bound, remains in the denominator, and is reported by censoring reason.
For a finite interval `[L, U]`, pinball-loss lower and upper contributions are respectively the minimum and maximum of the standard quantile loss over all `y` in `[L, U]`, computed analytically from `L`, `U`, and `q_tau`.
Primary pinball reporting is conditional on finite `ARRIVED` intervals, reports their frozen weighted mass and the excluded censored mass, and may not be described as unconditional full-population loss.
Neither metric may substitute an observation timestamp for latent arrival.
The UI may display a quantile as a model estimate, but its methodology view must expose that evidence limitation whenever only conditional quantile validation is available.

Primary metrics use the frozen primary-evidence label definition.
Also publish:

- Outcome-resolution rate overall and by required slice.
- Every censoring reason and its frequency.
- Arrival-interval width and observable source-observation-to-product-availability lag by evidence mode, line, station, and selected policy.
- Best-case and worst-case bounds that assign unresolved outcomes in favor of and against Arrive90.
- A Trip Update fallback sensitivity analysis kept separate from primary results.

Do not claim policy superiority when the lower bound does not exceed zero, when the bound interval includes a reversed policy ordering, or when the Section 6.1 coverage gate fails.

### 22.3 Uncertainty

Use paired block bootstrap resampling by complete service day with at least 2,000 replicates for final comparisons.
Report 95 percent confidence intervals.
Do not treat correlated stop events as independent examples.
If pre-test autocorrelation diagnostics show that service-day blocks are insufficient, freeze week blocks before the final test and retain the service-day result as a sensitivity analysis.

## 23. Prospective shadow protocol

Run the immutable live collector for a 28-service-day operational shakeout before freezing the final shadow panel.
The final prospective evidence period has a predeclared fixed end date at least 56 additional service days later, selected from a conservative sample-count and precision calculation.
If realized support or precision is insufficient at that fixed end date, report insufficient evidence and register a new future panel rather than extending the completed panel after inspecting outcomes.

Before examining outcomes:

- Freeze a panel of origin, destination, query time, exact five-minute deadline slack, reliability target, and maximum-extra-time cap scenarios.
- Assign target and cap through the same ordered 63-member inventory and frozen balancing algorithm used by the offline public request population.
- Freeze candidate-generation configuration.
- Freeze the promoted model and calibrator.
- Freeze the `historical_v1` feature schema and online-offline parity fixtures.
- Freeze feed freshness thresholds.
- Freeze the decision policy.
- Freeze a separate nonserving `SHADOW_095_EVIDENCE_V1` policy that uses the identical candidate generator, scores, 20-minute cap, 0.95 selection tuple, and outcome resolver but treats only the 0.95 target and `[0.95, 1.00]` cells as provisionally supported for shadow selection.
- The shadow-only policy never changes the serving eligibility manifest, never returns a user-visible recommendation, and never contributes a passing result to the current acceptance version.

Generate shadow recommendations at the scheduled query times.
Do not send recommendations to real riders as an experiment.
Resolve outcomes after the trains finish.

The shadow manifest is timestamped and hashed before its first scheduled query.
It specifies at least 2,000 distinct planned serving-policy decisions, at least 1,000 distinct base queries, at least 800 planned `SHADOW_095_EVIDENCE_V1` selections from at least 400 distinct base queries and 56 service-day blocks, the band-specific counts and service-day support in Section 6.1, and a desired block-bootstrap 95 percent confidence-interval half-width no greater than 0.03 for the primary deadline-success difference.
If the shadow-only 0.95 counts or calibration evidence fail, the result is immutable `INSUFFICIENT_EVIDENCE` or `FAILED` and cannot be repaired by extending the completed panel.
If they pass, the matured panel may become pre-test support evidence for a new acceptance version, but the 0.95 cells remain serving-ineligible until that version freezes a new manifest and passes an untouched later final-test interval.
The precision calculation uses service day as the sampling unit and the larger variance estimate from historical blocks or the pre-panel shakeout blocks.
It reports the assumed intraday correlation, number of independent service-day blocks, attrition, distinct queries, raw decisions, weighted mass, and cluster-adjusted effective sample size.
The panel end date is fixed from those planned counts and a conservative pre-study attrition allowance rather than extended in response to favorable or unfavorable observed outcomes.

Report:

- Prospective calibration.
- Deadline success.
- Feed freshness and outage rate.
- API and candidate latency.
- Abstention and degraded-mode rate.
- Difference from historical replay results.
- Failure case narratives.

Collector outage and missed-query denominators are fixed:

- Every scheduled panel query counts in end-to-end availability.
- A collector outage, router failure, or model failure counts as an abstention or unavailable result rather than disappearing.
- Predictive and decision metrics use resolved primary outcomes, accompanied by the full-panel censoring bounds and availability metric.
- Every produced selected-policy prediction remains in its prospective calibration band when its outcome is unresolved, and the Section 6.1 complete-band calibration bound is the only prospective calibration gate.

The first prospective report evaluates the frozen `historical_v1` model.
Do not train `prospective_v2` on this final shadow panel.

## 24. Live collection and freshness

The collector must:

- Fetch each configured feed on a documented cadence.
- Record the local fetch time and source header time separately.
- Hash and retain original bytes.
- Detect duplicate content without dropping fetch metadata.
- Validate protobuf structure.
- Compute feed age.
- Detect future-dated headers, regressing headers, source clock skew, and entity timestamps newer than their feed header.
- Quarantine malformed snapshots.
- Back off on source errors with jitter.
- Expose health metrics without logging full payloads.
- Reject a GTFS Realtime response larger than 64 MiB before protobuf parsing and reject more than 500,000 feed entities or a parse taking longer than ten seconds.
- Download historical or schedule archives through a 512 MiB compressed-size cap, an 8 GiB expanded-size cap, and a 64-to-1 expansion-ratio cap unless a new acceptance version raises a source-specific limit before observing the rejected object.
- Reject absolute archive members, parent-directory traversal, links, duplicate normalized paths, device files, and output paths outside a fresh extraction root.
- Apply object-store daily and total quotas with an explicit `QUOTA_EXCEEDED` collector state rather than deleting immutable evidence silently.

Initial freshness categories are:

- Fresh at no more than 90 seconds old.
- Stale between 90 seconds and a configured hard cutoff.
- Unusable beyond the hard cutoff.

The exact thresholds must follow current feed guidance and observed behavior.
Feed-header freshness, maximum-entity freshness, route-entity freshness, and the age of each candidate feature are calculated separately.
A fresh feed header or unrelated fresh vehicle never makes a stale or missing candidate feature appear fresh.
The decision's live status is the worst applicable freshness state among the evidence actually used by the selected candidate.
The UI always displays stale or degraded state.
The acceptance charter freezes thresholds after the 28-service-day shakeout and before the final shadow panel.
Changing them later creates a new shadow protocol version and does not replace the original result.

## 25. HTTP and SSE API

```text
GET  /v1/stations
GET  /v1/system/status
POST /v1/journeys/search
POST /v1/trips
GET  /v1/trips/{trip_id}
POST /v1/trips/{trip_id}/state
GET  /v1/trips/{trip_id}/events
POST /v1/trips/{trip_id}/stop
GET  /v1/models/active
GET  /v1/methodology
```

`POST /v1/journeys/search` accepts:

```text
origin_station_id
destination_station_id
ready_at
deadline
reliability_target
maximum_extra_minutes
```

The public V1 request requires supported distinct stations and applies the server-owned cutoff, ready-time normalization, conservative deadline-grid normalization, model-supported lead interval, and static-schedule future fallback in Section 12.4.1.
The server captures the cutoff before semantic validation and uses it for every source read, while the client supplies no authoritative query or cutoff timestamp.
The raw `ready_at` may range from two minutes before the cutoff through 24 hours after it, and the requested deadline must be from five through 180 minutes after the resulting `effective_ready_at`.
The server derives the conservative five-minute-grid `effective_deadline_at` under Section 12.4.1 before candidate generation, and every scoring or policy path uses that timestamp.
`reliability_target` is exactly 0.80, 0.90, or 0.95, and `maximum_extra_minutes` is an integer from zero through 20.
Every V1 search evaluates the same bounded universe of zero-transfer and one-transfer policies.
Requests outside those ranges fail before candidate generation, and accepted requests beyond the 15-minute model-support horizon never enter the model scorer.
The response exposes normalization through `ready_time_status = NORMALIZED_TO_CUTOFF` or `AS_REQUESTED` and includes `READY_TIME_NORMALIZED_TO_CUTOFF` in `limitations` when applicable.
For the future-ready schedule-only branch, `target_status = DEGRADED_SCHEDULE_ONLY`, `model_version = STATIC_SCHEDULE_BASELINE_V1`, `support_status = UNSUPPORTED_READY_HORIZON`, `decision_id` and `decision_expires_at` are null, `trip_start_supported = false`, every model probability and quantile is null, and the interface asks the rider to search again within 15 minutes of readiness before starting a trip.

The response includes:

```text
request_id
decision_id or null
decision_expires_at or null
data_cutoff
requested_ready_at
effective_ready_at
ready_time_status
requested_deadline_at
effective_deadline_at
deadline_time_status
trip_start_supported
feed_status
model_version
candidate_generator_version
static_candidate_manifest_hash
decision_context_id
decision_context_version
eligibility_mask_hash
fastest_itinerary
recommended_itinerary
target_status
alternatives
explanation_codes
limitations
support_status
```

Only the `recommended_itinerary` response slot may include a supported deadline probability and supported quantile arrival times because output-support gates are defined on the initial selected-policy population.
The fastest-comparator slot, alternatives, and backup-itinerary slot expose schedule fields, planned and extra time, feed ages, deterministic transfer-buffer and backup-departure facts, and explanation codes, but their deadline probability and every arrival quantile are null with `model_output_status = NOT_SELECTED_OUTPUT_UNVALIDATED`.
This suppression applies to every non-recommended response slot even when it references the same immutable policy key as the recommendation, and clients use the `recommended_itinerary` slot for validated model outputs.
The selected recommendation includes transfer-support status, planned time, extra planned time, feed-header age, relevant-entity age, candidate-feature age, and distinct backup-departure and backup-itinerary details whether its supported model outputs are numeric or suppressed.
An authenticated trip-session update may add a nullable conditional transfer probability only after confirmed `AT_TRANSFER` no later than the initial effective ready time plus 210 minutes, and its label states that it estimates second-leg boarding during the exact next 15 minutes rather than end-to-end transfer completion.
A later confirmed `AT_TRANSFER` emits the deterministic unsupported-horizon status without invoking the transfer classifier or probability trigger.
A recovery decision payload includes its reason, schedule-derived recommendation, cap reference, continuation comparator, optional schedule-derived backup, and `recovery_status`, while every new deadline probability and arrival quantile is null and no reliability-target status is present.
Values use explicit timestamps and durations rather than locale-dependent strings.
The UI shows both deadline timestamps and the normalization limitation whenever they differ.

`POST /v1/trips` accepts only:

```text
decision_id
selected_itinerary_id
```

Trip creation rejects a null, unsupported-horizon, expired, consumed, otherwise non-trip-startable decision, or any `selected_itinerary_id` other than the exact server-recorded recommendation before creating session state.

`POST /v1/trips/{trip_id}/state` accepts only:

```text
idempotency_key
expected_state_version
next_state
boarded_itinerary_or_route_pattern_id or null
recovery_decision_id or null
```

Every non-loopback API deployment requires HTTPS, rejects plaintext HTTP, validates an exact Host allow-list, permits only exact configured same-origin browser origins, and sends no wildcard CORS response.
The deployment trusts forwarded client and scheme headers only from an explicit proxy-address allow-list and ignores them from every other peer.
The web application sends a restrictive Content Security Policy without `unsafe-inline` or `unsafe-eval`, loads no unreviewed third-party script, and uses explicit worker, connection, image, map-tile, and style origins.
It also sends HSTS, `Referrer-Policy: no-referrer`, `X-Content-Type-Options: nosniff`, a minimal Permissions Policy, and frame-ancestor protection.
Request bodies are limited to 32 KiB and every station identifier, timestamp, duration, reliability target, transfer limit, and extra-time limit is schema and range validated before router or model work begins.
The initial per-client limits are 30 journey searches per minute, ten trip creations per hour, 60 state changes per minute per authorized trip, and one active SSE stream per authorized trip.
Every queue and retained SSE history is byte, event-count, and age bounded.
Every journey-search response containing a decision capability, every trip-creation response, and every authenticated response uses `Cache-Control: no-store`.
Authorization failures use a constant-shape response and never reveal whether a public `trip_id` exists.
Decision-capability failures use the same constant-shape response for absent, malformed, unknown, expired, and consumed values and never reveal whether a decision record exists.
Access logs, traces, metrics, exception reports, and frontend error reports replace trip path parameters with a placeholder and never contain decision capabilities, trip bearer secrets, stations, ready times, deadlines, selected itineraries, state bodies, alert text, or SSE payloads.
Every state-changing request requires the exact allowed Origin, trip creation treats its decision capability as bearer authorization, every later trip mutation additionally requires the trip bearer header, and the server rejects cross-site and missing-Origin browser mutations.
Development servers before this contract passes bind only to loopback and display a non-production warning.

SSE events include full sequence numbers, the live-update event kind and cutoff, model and data versions, static candidate-manifest hash, decision-context identifier, source-attempt lineage, freshness state, and value-provenance classification from Section 18.1.
Authenticated fetch-stream clients reconnect with the last event identifier and bearer header and receive a current snapshot if the retained event window has expired.

## 26. Product interface

The web application contains:

- Station origin and destination selectors.
- Ready-time and deadline controls.
- Visible requested and effective ready times whenever the server normalizes a past-ready request.
- Reliability target and maximum-extra-time controls.
- System-status and feed-age indicator.
- Network map.
- Fastest and safer decision cards.
- Arrival distribution visualization with tabular alternative.
- Transfer buffer and backup timeline.
- Deterministic explanation list.
- Live trip state.
- Conditional recovery decision card tied to a confirmed or explicitly assumed trip state and labeled as a schedule action without a new deadline probability or reliability-target claim.
- Visible degraded, stale, abstained, and unsupported states.
- A disabled trip-start action with a search-again-near-readiness instruction for the future-ready schedule-only branch.
- Methodology and limitations links near the probability display.

The interface must remain usable without a map.
All probability and time information needs accessible text.
Color cannot be the only distinction between safer and fragile itineraries.

## 27. Operational monitoring

Metrics include:

- Feed fetch success and duration.
- Feed age by type.
- Empty, malformed, and duplicate snapshots.
- Entity count and missing-field drift.
- Schedule-graph age.
- Candidate-generation success and latency.
- Risk-scoring p50, p95, and p99 latency.
- SSE session count and reconnect rate.
- Degraded-mode and abstention rate.
- Model and feature schema version.
- Delayed outcome volume.
- Primary-label evidence coverage and censoring by reason.
- Rolling Brier score on matured outcomes, complete-band calibration bounds including unresolved outcomes, and resolved-only calibration diagnostics.
- Subgroup volume and drift.

Calibration monitoring is delayed until outcomes resolve.
No alert may pretend that an unlabeled live probability is already known wrong.

## 28. Failure behavior

### Feed unavailable

Use schedule-only candidates and baselines when defensible.
Label the result degraded and do not reuse expired live values as current.

### Candidate router unavailable

Return a stable unavailable state.
Do not manufacture an itinerary from stale cache unless cache age and graph version remain within an explicit policy.

### Model unavailable or incompatible

Fall back to a named baseline.
Return the fallback model identifier and limitation.

### Sparse support

Widen uncertainty or abstain.
Do not map an unsupported line or station to a superficially similar category without reporting it.

### Initial ready horizon outside model support

For an effective ready lead greater than 15 minutes and no more than 24 hours, return only the static-schedule result defined in Section 12.4.1.
Do not score the learned model, reuse current operational features as future evidence, emit a probability or quantile, or create a trip-startable decision, while a causally known alert may still annotate or mask a static candidate when its active period covers that itinerary.
A request outside the full accepted ready-time range fails before candidate generation.

### Missing outcome

Censor the evaluation row with a reason.
Do not label missing arrivals as failures or successes by default.
Keep the query in end-to-end availability and censoring-bound denominators.

### Proven non-arrival within the horizon

When complete source observation proves no eligible arrival through the frozen horizon, record `PROVEN_NO_ARRIVAL_WITHIN_HORIZON` rather than censoring the journey.
It is a resolved deadline failure for every deadline no later than that horizon and contributes one right-censored AFT interval from the horizon through positive infinity rather than fabricated zero-valued threshold rows.

### Alert ambiguity

Ignore an alert as a model feature unless its publication time and affected entities are defensible.
It may still be displayed to the user as an official alert with its source timestamp.

## 29. Testing strategy

### 29.1 Data-contract tests

- GTFS service-day times beyond 24:00.
- Daylight-saving transitions.
- Schedule version boundaries.
- Duplicate and malformed protobuf snapshots.
- Repeated identical blobs with distinct fetch attempts.
- Stale feed headers.
- Future-dated, regressing, and clock-skewed feed headers.
- Entity timestamps newer than the feed header.
- Empty versus failed feed.
- Complete, incomplete, and unknown observation-coverage windows.
- A dense route-level window with one unaccounted eligible train remains `INCOMPLETE`.
- Every potentially eligible train is reconciled before historical completeness is allowed.
- Query generation retains service dates and queries with incomplete or unknown observation coverage inside the frozen interval and aggregate scope.
- A per-day, per-query, disruption, outage, ambiguity, or outcome-completeness exclusion fails manifest validation.
- Exact, ambiguous, and unmatched planned trips.
- Missing arrivals and departures.
- Vehicle Position observation timestamps produce arrival bounds rather than fabricated exact arrivals.
- Departure, travel-time, dwell-time, and lateness fields preserve their bounds rather than collapsing to point timestamps or durations.
- Oversized protobuf, entity flood, parse timeout, zip bomb, expansion-ratio breach, path traversal, absolute path, link, duplicate normalized member, and object-store quota cases.
- Station and parent-station normalization.
- Initial live cutoff capture, past-ready normalization, conservative effective-deadline normalization, and the model-supported versus schedule-only ready-horizon boundary.

### 29.2 Temporal leakage tests

- A `TemporalView` rejects records after the cutoff.
- Feature modules cannot import outcome-storage modules.
- Deliberately shifted future events cause a test failure.
- Fitting preprocessing on validation or test dates fails a manifest check.
- Schedule versions published or made active after the query cutoff are unavailable.
- Schedule versions not known by the query cutoff are unavailable even when they later cover that service date.
- Final stop count, fallback static match, next-stop departure, later alert revision, and post-journey aggregate defects each fail a seeded test.
- A row with an early event timestamp but a later retrospective match or publication time is unavailable before that later time.
- Unsupported copying of event time into product-availability time fails contract validation.
- Realized-event timestamps satisfy the declared ordering or receive an explicit quarantined clock-skew classification.
- Prospective prediction features are absent from historical rows without captured snapshots.
- A `historical_v1` bundle containing any Trip Update prediction feature fails registry validation.
- An initial search cannot observe a feed, alert, schedule version, or feature that becomes product-available or is fetched after its server-owned cutoff, even when processing finishes after that record arrives.
- Client-supplied timestamps cannot move the initial cutoff, expand the 15-minute model-support horizon, or make a later source record visible.

### 29.3 Outcome-oracle tests

- Direct journey.
- Successful transfer.
- Missed transfer and next-train boarding.
- No eligible train.
- Ambiguous trip match.
- Missing destination event.
- Transfer walk exactly at departure boundary.
- A negative transfer label requires complete per-train reconciliation through the frozen transfer window.
- An ambiguous or incomplete transfer window is censored and remains in coverage and complete-population bounds.
- Same timestamp tie-breaking.
- Actual departure before rider readiness with a later downstream move event does not satisfy observed stop presence.
- A direct `STOPPED_AT` observation at the boarding platform after readiness satisfies the frozen virtual-rider stop-presence assumption without claiming observed door state.
- Ready-to-board time receives no additional first-leg access margin.
- A deadline before the arrival lower bound is an identified failure.
- A deadline at or after the arrival upper bound is an identified success.
- A deadline inside the arrival interval remains interval-unresolved.
- Complete observation with no eligible arrival produces `PROVEN_NO_ARRIVAL_WITHIN_HORIZON`.
- Incomplete source observation produces `CENSORED` rather than a proven failure.
- Same route-pattern policy with different scheduled departures deduplicates.
- Different route-pattern policies retain different outcomes.
- Short-turn, skipped-stop, canceled, replacement, added, and non-revenue train rules.
- Vehicle Position primary evidence versus Trip Update fallback sensitivity evidence.
- Direct stop-observation interval versus conservative downstream-move interval semantics.
- A trip scheduled before readiness but delayed into qualifying observed stop presence after readiness is considered by the oracle reconciliation set.
- The canonical schedule-feature trip never prevents the oracle from boarding an earlier scheduled trip that is realized later.

### 29.4 Model tests

- Deterministic training under a fixed seed and environment.
- Stable feature ordering and schema.
- Quantile ordering.
- Deadline-probability monotonicity.
- Deadline-probability and inverse-quantile consistency from the same CDF.
- Exact, left-open right-closed interval, right-censored, and excluded-censored AFT likelihood fixtures.
- Raw-margin normal, logistic, and extreme-value AFT CDF fixtures that fail if a default predicted event-time label is interpreted as a probability.
- Nonpositive, reversed, nonfinite-lower, and illegally infinite-upper AFT bounds quarantine instead of being clipped.
- Survival probability beyond the observation horizon produces an unresolved quantile rather than a fabricated time.
- Every exposed deadline lies on or between supported frozen grid points.
- Scoring outside the grid returns `INSUFFICIENT_EVIDENCE` rather than extrapolating.
- Calibrator serialization.
- Transfer logistic and gradient-boosted classifier fixtures use one deadline-independent weighted row per transfer candidate.
- Initial journey searches keep transfer probability null, and only a confirmed server-timestamped `AT_TRANSFER` state can request the conditional second-leg probability.
- A candidate that never reaches `AT_TRANSFER` remains in end-to-end deadline and reachability metrics but never becomes a negative conditional transfer label.
- Transfer calibrator selection, fit-window separation, monotonicity, serialization, fixed-decile assignment, and unresolved-outcome calibration-bound fixtures.
- Values immediately below, exactly on, and immediately above every transfer-decile boundary reproduce six-decimal half-even rounding, decile assignment, and strict below-0.50 trigger behavior while calibration means retain unrounded predictions.
- A censored transfer window stays in its decision-time decile denominator but never enters classifier or calibrator loss.
- A transfer reached exactly at or near the 210-minute journey horizon requires source completeness through its own `transfer_ready_at_utc + 900 seconds`, and a truncated follow-up is censored rather than failed.
- Extending transfer follow-up beyond the journey horizon never changes the deadline-arrival label or deadline censoring boundary.
- A second-leg trip scheduled just after the journey horizon but within the 900-second transfer follow-up belongs to the hashed transfer-reconciliation set and can resolve the transfer outcome without changing the journey outcome.
- A trip scheduled after either window cutoff but observed early inside that window is already present in the applicable all-service-day reconciliation superset and is resolved under the ordinary observed-presence rule.
- A trip scheduled after a cutoff with no qualifying in-window observation remains a reconciled nonsuccess and cannot create boardability from schedule alone.
- A transfer probability is null when its candidate-population decile or station gate fails, and its probability-based recovery trigger is disabled when any candidate-population, station, or selected-policy decile gate fails.
- Missing-feature behavior.
- Sparse category behavior.
- Model-bundle compatibility failure.
- Golden scoring fixture.

### 29.5 Decision tests

- Target met by fastest route.
- Target met only by a slower route.
- No route meets target.
- Maximum-extra-time prevents an excessive detour.
- Static-fastest selection, planned time, extra planned time, and cap eligibility reproduce the frozen formulas after alert masking.
- Candidates outside the cap are removed before target-met and target-not-met ranking.
- Requested-target support is the exact conjunction of the static initial ready-horizon rule, request-level declared-target support, candidate's own predicted band, and every applicable line and parent-station deadline slice, and an absent or ineligible member prevents target-qualified ranking.
- Table-driven requested-target fixtures independently make the static initial ready-horizon rule, request-level target cell, predicted band, each deadline-slice kind, and an applicable cell lookup fail and require the conjunction to return false.
- An ineligible 0.95 target cell returns `INSUFFICIENT_EVIDENCE` even when no cap-eligible candidate reaches 0.95, so an empty set cannot make target support pass vacuously.
- Quantile eligibility never changes target-qualified or fallback-supported membership.
- An empty fallback-supported set returns the static-fastest schedule recommendation with no unsupported model probability.
- Exact and quantized-probability ties reproduce the frozen target-met and target-not-met tuples and bytewise policy-key order.
- Values immediately below, exactly on, and immediately above every deadline-band boundary reproduce six-decimal half-even rounding, canonical band assignment, target qualification, and fixed-point population membership.
- A changed causally known alert mask recomputes the shared comparator and extra-time reference without mutating the static candidate manifest.
- Backup departure and backup itinerary selection remain distinct, deterministic, and null when their frozen candidate set is empty.
- Stale data selects degraded mode.
- Model abstention remains visible.
- Recovery route replaces a failing transfer.
- Every permitted direct, transfer, stop, recovery-activation, repeated-recovery, terminal, same-state, rollback, wrong-itinerary, stale-version, and cross-trip transition reproduces the frozen state graph.
- A trip initiated with a nonconfirmatory target or cap cannot fire the probability-based recovery trigger but can still run a closure-triggered recovery search.
- Trip creation accepts only the server-recorded recommended itinerary, and fastest, backup, or alternative identifiers fail before capability consumption or session creation.
- Recovery cutoff and ready time use the accepted server transaction timestamp and ignore forged or skewed client timestamps.
- Simultaneous recovery reasons reproduce the frozen closure-before-low-probability precedence.
- The original continuation remains the recorded evaluation comparator and is never selectable as its own recovery action.
- A closure-masked continuation causes the static-fastest eligible distinct recovery candidate to become the cap reference.
- An eligible continuation remains the cap reference for low-probability recovery, including fixtures where a recovery candidate has negative extra planned time.
- Recovery selection never invokes the arrival-time CDF, returns null new deadline probabilities and quantiles, omits reliability-target status, and reproduces the frozen schedule-only ranking and backup tuples.
- A supported low-transfer-probability trigger can authorize recovery but cannot change recovery candidate scores or ordering, and an unsupported trigger cell cannot authorize that trigger.
- V1 rejects an unknown or manual recovery reason and never includes a hypothetical user-request event in the frozen recovery evaluation.
- A low-probability recovery trigger is possible only for the exact transfer recommendation selected by the confirmatory 0.90-target and 20-minute-cap policy and represented in the frozen selected-policy trigger decile.
- After any recovery activation, a later supported conditional transfer estimate cannot fire the low-probability trigger, while a causally known closure may still issue a repeated recovery action through the frozen state graph.
- An empty eligible distinct recovery set returns `NO_DISTINCT_RECOVERY_ACTION`, preserves the original continuation, and emits no `recovery_decision_id`.
- Unsupported 0.95 target returns `INSUFFICIENT_EVIDENCE`.
- Candidate-row calibration cannot substitute for initial selected-policy calibration.
- Global calibration cannot authorize `TARGET_MET` when the static initial ready-horizon rule, candidate's own band, a line, origin, destination, or transfer slice, or a declared target-specific cell fails its applicable rule.
- A candidate-generator mode that differs from the bundle's evaluated mode fails registry validation.
- A causally known closure changes only the decision-context eligibility mask and never mutates the static candidate manifest.
- Missing, later, or mismatched alert lineage fails decision-context replay and bundle validation.
- The four resolved and unresolved policy-outcome combinations reproduce the frozen `Delta_lower` and `Delta_upper` formulas.
- Every unresolved initial selected-policy decision remains in its original calibration band and reproduces the frozen `success_lower`, `success_upper`, and worst-case calibration-gap formulas.
- A pre-test-ineligible deadline band, deadline slice, or target-specific cell is suppressed by the frozen decision kernel before final-test access.
- A deadline band, deadline slice, or target-specific cell frozen as eligible that fails a final-test gate leaves the eligibility manifest unchanged, marks the policy version failed, and cannot be suppressed into a passing result.
- A revised deadline eligibility manifest is rejected unless it names a new acceptance version and an untouched future test interval disjoint from the failed policy's opened outcomes.
- A pre-test-ineligible transfer candidate decile, transfer station, selected-policy trigger decile, or quantile level remains suppressed by the frozen decision kernel after final-test access.
- A transfer cell, trigger cell, or quantile level frozen as eligible that fails an applicable final-test gate leaves the output-support eligibility manifest unchanged, marks the complete policy version failed, and cannot be suppressed into a passing result.
- A revised transfer-cell, trigger-cell, or quantile-level eligibility manifest is rejected unless it names a new acceptance version and an untouched future test interval disjoint from the failed policy's opened outcomes.
- Eligibility discovery starts with every declared deadline band, deadline slice, target-specific cell, transfer cell, trigger cell, and quantile cell provisionally eligible, removes every failing cell simultaneously, never re-enables a removed cell, and reaches the same stable manifest regardless of cell enumeration or worker completion order.
- Every eligibility iteration evaluates exactly the frozen assigned public request-lattice member for each deadline variant with its full weight, independent of the separately scored primary policy.
- A cascading fixture in which one round's removals make another cell fail requires a later simultaneous removal round and terminates within the declared `N + 1` iteration bound.
- A fresh-process verification run reproduces every initial decision, population, metric, removal-set, manifest, and discovery-artifact hash before final-test access.
- Recovery schedule actions never enter deadline-band, deadline-slice, target-cell, or quantile eligibility populations.
- A secondary target cannot satisfy the primary gate, and its significance uses the frozen Holm ordering.
- Balanced-assignment support rows never replace the independently scored full deadline-variant population in a primary or registered secondary policy contrast.
- A qualifying model-free bundle is identifiable in every response and artifact.
- A supported probability below 0.80 enters its exact fixed band, while a failing static initial ready-horizon rule, low-probability band, or applicable slice suppresses the numeric probability and returns `INSUFFICIENT_EVIDENCE`.
- A raw ready time within the two-minute past tolerance is normalized visibly to the server cutoff and never creates a historical-contract row with readiness before query time.
- An effective ready lead of exactly 15 minutes may enter model scoring, while a greater lead through 24 hours returns only the frozen schedule result with null model outputs and no trip-startable decision.
- Requested deadlines exactly five and 180 minutes after effective readiness remain unchanged, an intermediate non-grid deadline normalizes downward to the nearest supported five-minute slack, a result below five minutes fails, and every effective deadline maps to one historical decision scenario.
- A ready lead beyond 24 hours or before the past tolerance fails before candidate or model work.
- A post-start snapshot never invokes the initial arrival CDF or changes its frozen deadline probability or quantiles.
- A live `AT_TRANSFER` cutoff exactly at the initial 210-minute support boundary may invoke the conditional transfer model, while a cutoff one microsecond later returns `UNSUPPORTED_TRANSFER_READY_HORIZON` with null probability and no probability trigger.
- A deterministic closure update marks the selected policy unsupported and prompts a new initial or recovery search without reranking frozen scores.
- Official Trip Update annotations, frozen initial estimates, conditional transfer estimates, and recovery decisions retain distinct value-provenance classifications in every API and SSE fixture.
- A mid-journey deadline-rescoring bundle without its own state-conditioned schema, support, calibration, final-test artifact, and registry identifier fails bundle validation.

### 29.6 Integration tests

- Raw feed to normalized partition.
- Schedule archive to historical graph.
- Audit enumerator to OpenTripPlanner candidate-recall report.
- Query to features to outcome.
- Training to registry to API.
- OpenTripPlanner candidate to normalized candidate.
- Live snapshot to deterministic freshness, official-estimate, closure, conditional-transfer, or recovery update without unsupported initial-CDF rescoring.
- SSE disconnect and resume.
- Collector restart without snapshot loss.

### 29.7 Browser tests

- Complete direct journey search.
- Complete one-transfer search.
- Compare fastest and safer cards.
- Start and stop a live trip.
- Receive a recovery update.
- Confirm trip state before receiving nonconditional recovery guidance.
- Observe stale-feed warning.
- Observe no-target-met state.
- Observe a supported below-0.80 `TARGET_NOT_MET` probability and an unsupported low-band schedule-only suppression state.
- Observe visible ready-time normalization and the non-trip-startable future-ready schedule-only result.
- Use the workflow by keyboard and screen-reader landmarks.

### 29.8 Session and API security tests

- A public `trip_id` without its bearer secret cannot read, mutate, stop, or subscribe to a trip.
- The bearer secret appears once in the creation response and never appears in storage, URLs, logs, traces, metrics, browser persistence, or cacheable responses.
- Bearer digest comparison is constant-time and key-version rotation preserves only the bounded declared verification window.
- A trip can be created only from an unexpired server-owned decision and an itinerary returned by that decision.
- A future-ready schedule-only response has no decision capability and cannot create a trip session.
- A decision capability has 256 bits of cryptographic randomness, is consumed atomically once, expires after ten minutes, and yields at most one trip under concurrent creation attempts.
- Only a versioned keyed HMAC digest of the decision capability is stored, and key rotation retains an old decision key no longer than the capability expiry window.
- A decision capability remains memory-only in the browser and is absent from URLs, cookies, browser persistence, history, caches, analytics, logs, traces, metrics, exception reports, and frontend error reports.
- Journey-search responses containing a decision capability use `Cache-Control: no-store`, and absent, malformed, unknown, expired, and consumed capabilities produce the same constant-shape failure.
- Trip creation requires the exact allowed Origin and the decision capability, and replay, cross-site consumption, and concurrent consumption cannot create or reveal another session.
- Forged model, candidate-manifest, eligibility-mask, or route-pattern metadata is ignored or rejected.
- State mutations enforce the transition graph, itinerary membership, idempotency key, and optimistic state version under concurrent requests.
- The persisted transition and SSE event remain atomic under an injected failure.
- A stopped, expired, or deleted trip rejects its former bearer secret.
- A bearer for one trip cannot access another trip.
- A recovery decision identifier cannot be consumed by another trip, state version, station, or bearer, and concurrent consumption activates at most one recovery itinerary.
- An SSE reconnect requires the bearer and cannot exceed the one-stream or retained-history bounds.
- Plaintext non-loopback startup, hostile Host, hostile Origin, wildcard CORS, oversized bodies, invalid timestamps, unsupported station identifiers, excessive durations, and rate-limit violations fail before router or model work.
- Forwarded headers from an untrusted peer cannot spoof HTTPS or client identity.
- The production response contains the frozen CSP, HSTS, referrer, MIME, frame, and permissions headers and loads no unreviewed third-party script.
- Alert text and every server-controlled string remain inert under browser rendering and cannot read the in-memory bearer.
- Access-log and failure-response fixtures contain no station, ready time, deadline, itinerary, state body, decision capability, trip bearer secret, or distinguishable decision-record or trip-existence signal.
- Trace, metric, exception-report, and frontend-error fixtures contain the same sensitive-field exclusions.

## 30. Performance targets and measurement

Targets are provisional until measured on named hardware.

Initial targets are:

- Supported initial and conditional-transfer scoring plus deterministic recovery selection under 100 milliseconds p95 for a bounded candidate set.
- Cached station-to-station plan request under one second p95.
- No unbounded request, collector, or SSE queues.
- Collector memory bounded independently of retained raw-object volume.
- Deterministic offline query generation from a fixed manifest.

The reference machine, CPU allocation, memory limit, storage mode, container versions, warm-up, concurrency, and frozen request corpus belong in `configs/acceptance/v1.yaml` before performance measurements begin.
The gate may not be revised after seeing a failed benchmark.
A later protocol version may use different hardware, but it must preserve the original result and make no direct comparison without normalization.

Benchmark:

- One, five, and ten candidates.
- Fresh, stale, and absent live feeds.
- Cold and warm model loads.
- Concurrent search and SSE sessions at documented local scale.
- One day, one month, and one year of replay generation.

Publish p50, p95, p99, throughput, memory, hardware, versions, and workload manifests.
Do not claim city-scale capacity from a single synthetic throughput number.

## 31. Milestone plan

### Milestone 0 - Source, label, license, and acceptance feasibility

Deliverables:

- Initialize repository, locks, CI, formatting, linting, typing, and tests.
- Read the current LAMP data dictionary, LAMP transformation source, GTFS specifications, and MassDOT developer license.
- Inventory and hash metadata for every source object and schedule version in the complete proposed historical interval.
- Compare schema fingerprints, publication metadata, and partition-quality indicators across the complete proposed interval.
- Download and inspect at least 30 consecutive representative service days, at least one complete service week selected by a frozen rule from every calendar month in the proposed interval, and stratified samples around every schema change, schedule-version boundary, daylight-saving transition, year boundary, and major documented service discontinuity.
- Compare public-export fields with LAMP transformation logic, including Vehicle Position and Trip Update timestamp provenance and the feasibility of a next-stop Vehicle Position departure upper bound.
- Build the field-level historical provenance ledger and prove product-availability time without copying event time by assumption.
- Quantify arrival-interval width, observed stop presence, destination evidence, per-train reconciliation completeness, trip matching, censoring, causal-feature support, route coverage, transfer coverage, and source latency by required slice.
- Manually reproduce at least 100 deterministic sampled direct queries per supported line and at least 25 transfer queries per proposed transfer station.
- Define and golden-test the exact virtual-rider oracle, typed route-policy action, canonical schedule simulation, exceptional-trip table, arrival-interval rule, and per-train complete-observation reconciliation rule.
- Commit `configs/acceptance/v1.yaml`, the contiguous historical date interval, query seed, chronological split rule, aggregate line and station allow-list, initial server-owned cutoff rule, ready-time and deadline normalization rules, horizon fallback rules, and temporal-semantics decisions before model work.
- Prove that no individual service date, query, disruption, source outage, ambiguous trip, or incomplete outcome window can be removed by the query generator.

Acceptance gate:

- The Section 6.1 audit-candidate resolution, arrival-interval width, observed-stop-presence, censoring, and causal-feature support thresholds pass on the audit sample.
- Every manually reproduced feature is proven available to an online-equivalent implementation by its query cutoff.
- Event time is never used as product-availability time without ledger evidence.
- Direct Vehicle Position observation intervals, conservative Vehicle Position upper-bound intervals, and Trip Update fallback sensitivity outcomes are distinguishable.
- Exactly one primary outcome-time semantic is frozen and used consistently throughout each reported result set.
- `PROVEN_NO_ARRIVAL_WITHIN_HORIZON` is distinguishable from `CENSORED` through per-train and per-path reconciliation rather than aggregate route density.
- A downstream move upper bound is never accepted as boarding evidence.
- Schedule knowledge time follows verifiable listing or publication evidence and never selects a version unknown at the query cutoff.
- License and attribution requirements are documented.
- The source-by-artifact redistribution and retention matrix is complete, and every planned public artifact is either permitted or replaced by a permitted regeneration path.
- `make check` and `make gate MILESTONE=0` pass from a clean checkout.

Kill gate:

- If public data supports neither direct Vehicle Position stop evidence nor the conservative next-stop Vehicle Position upper-bound mode at the frozen coverage thresholds after narrowing scope, stop the recommendation model and select another ML product.
- If observed stop-presence evidence cannot meet the frozen audit-candidate coverage threshold, stop the virtual-rider recommendation model even when destination interval coverage is high.
- If no operational historical feature family passes its provenance and support gates, freeze `historical_v1` as schedule-only or wait for prospective training data before making learned live-feature claims.

### Milestone 1 - Immutable ingestion, primitive temporal store, and collector

Deliverables:

- Implement `FeedBlob`, `FetchAttempt`, and `HistoricalSourceObject` storage and manifests.
- Implement schedule-archive ingestion with publication and knowledge times.
- Normalize direct and conservative-upper-bound Vehicle Position evidence without hiding Trip Update fallback.
- Implement alert-revision history.
- Implement the GTFS Realtime collector with separate transport, parse, semantic, and freshness states.
- Implement content hashing, fetch-attempt retention, quarantine, retry, clock-skew detection, and metrics.
- Implement frozen compressed, expanded, entity-count, parse-time, extraction-path, expansion-ratio, and object-store quota limits.
- Implement `TemporalView` over the primitive allow-list.
- Implement `ObservationCoverageWindow`, per-train historical reconciliation, and conservative prospective completeness rules.

Acceptance gate:

- Identical manifests create byte-equivalent normalized partitions.
- Repeated identical feed bytes produce one blob and multiple retained fetch attempts.
- Malformed, empty, stale, failed, clock-skewed, and quarantined states remain distinguishable.
- Missing fetch attempts, an unreconciled eligible train, a missing relevant stop interval, excessive source gaps, and unknown historical partition quality prevent an observation window from becoming `COMPLETE`.
- Route-level density alone cannot produce a historical `COMPLETE` state.
- Zip bombs, oversized protobufs, path traversal, links, duplicate normalized paths, entity floods, parser timeouts, and quota exhaustion fail into explicit bounded states.
- Every seeded direct and indirect future-access defect fails.
- Collector restarts do not overwrite or skip acknowledged attempts.
- `make gate MILESTONE=1` passes.

### Milestone 2 - Historical graph, candidate recall, and frozen query population

Deliverables:

- Build the pinned historical OpenTripPlanner graph before labels or models.
- Implement `TransitLeg` and normalized `CandidateItinerary` contracts.
- Implement route-pattern deduplication and the static audit enumerator.
- Implement canonical schedule simulation, eligible-trip-set hashing, and the frozen exceptional-trip decision table.
- Produce deterministic candidate-recall fixtures for direct, transfer, branch, short-turn, unsupported-connectivity, and more-than-16-policy truncation cases.
- Enumerate every timetable and connectivity equivalence class used by the complete frozen query population.
- Generate the Section 13.1 query population and freeze its manifest before resolving outcomes.
- Freeze transfer-walk rules and unsupported station connectivity.

Acceptance gate:

- Historical candidate generation is identical after a process and container restart.
- The population corpus recovers the static fastest route for every supported query and meets the overall and slice route-pattern recall gates.
- Same-policy departure alternatives deduplicate and genuinely different policies remain separate.
- Canonical scheduled features are identical regardless of which duplicate OpenTripPlanner departure response is encountered first.
- The query manifest contains at least 100 distinct base origin-destination pairs and no outcome-derived inclusion decision.
- Every scheduled query inside the retained date interval and aggregate scope remains represented in availability and censoring denominators even when its source window is incomplete.
- `make gate MILESTONE=2` passes.

### Milestone 3 - Causal features, virtual-rider outcomes, and baselines

Deliverables:

- Implement the exact online-equivalent feature registry and offline-online parity fixtures.
- Resolve virtual-rider outcomes and explicit censoring reasons from frozen candidates.
- Resolve observed stop-presence evidence separately from destination arrival intervals.
- Resolve `ARRIVED`, `PROVEN_NO_ARRIVAL_WITHIN_HORIZON`, and `CENSORED` without merging them.
- Produce primary Vehicle Position arrival intervals under the one frozen evidence semantic and separate Trip Update fallback sensitivity labels.
- Implement static, rolling, empirical, monotonic-logistic, point-model, fastest-candidate, and prospective Trip Update baselines where applicable.
- Freeze feature, outcome, weighting, and split manifests.

Acceptance gate:

- Every seeded leakage fixture fails through the real feature-builder path.
- `historical_v1` contains no unsupported Trip Update prediction feature.
- `full_candidate_resolution_rate` and censoring remain within the applicable Section 6.1 thresholds on the full frozen query population.
- Arrival-interval width and per-train reconciliation gates remain within the applicable Section 6.1 thresholds on the full frozen query population.
- Baseline reports use identical static candidates, decision-context masks, alert lineage, queries, weights, and outcome semantics.
- Best-case and worst-case censoring bounds are generated automatically.
- Arrival intervals, right-censored proven non-arrivals, and interval-unresolved deadlines enter only their valid interval-censored likelihood or evaluation paths.
- `make gate MILESTONE=3` passes.

### Milestone 4 - Coherent CDF, offline decision kernel, calibration, and model registry

Deliverables:

- Train the interval-censored AFT arrival-time model and transfer-explanation classifier.
- Select and calibrate the transfer classifier under its nested chronological protocol and generate complete-population decile and station support artifacts.
- Derive deadline probability and all displayed arrival quantiles from the one calibrated CDF.
- Run the predeclared nested calibrator comparison and fit the frozen calibrator on the dedicated calibration window.
- Implement support and abstention policy, model bundles, parity validation, and registry checks.
- Implement the side-effect-free initial decision kernel with the provisional eligibility mask, exact requested-target conjunction, probability quantization, comparator, cap, target-qualified and fallback-supported sets, statuses, ranking tuples, and backup rules before generating a selected-policy row.
- Materialize each deadline variant's one frozen balanced public request-lattice assignment with full weight, while separately scoring the primary and registered secondary policies over every deadline variant.
- Implement the monotonic simultaneous eligibility-discovery loop, its `N + 1` termination guard, fresh-process verification, and complete hash-chained discovery artifact.
- Produce predictive, initial selected-policy calibration, slice, transfer-cell, trigger-cell, quantile-level, support, and uncertainty reports from the final stable pre-test manifest without opening final-test labels.

Acceptance gate:

- Interval-likelihood fixtures, deadline monotonicity, CDF bounds, quantile ordering, supported-grid behavior, beyond-horizon handling, and probability-quantile consistency tests pass.
- Transfer-row weighting, classifier selection, calibration-window separation, unresolved-outcome bounds, decile support, station support, and probability-suppression tests pass.
- Canonical transfer rows remain deadline-independent, selected-policy trigger occurrences carry deadline-variant primary weights, and repeated occurrences retain distinct keys inside the same service-day bootstrap block.
- The offline decision kernel passes every Section 29.5 initial-selection fixture and deterministically materializes each iteration's initial selected-policy population used by eligibility discovery and calibration reports.
- Public-lattice inventory, within-split balance, seed reproducibility, unique assignment, constant-total-weight, full primary-population, and registered-secondary-population fixtures pass.
- The all-eligible seed, exact requested-target conjunction, fail-closed absent-cell behavior, simultaneous removal, absorbing ineligibility, cascading failure, `N + 1` termination, worker-order independence, and fresh-process hash-reproduction fixtures pass.
- Model and calibrator selection artifacts prove that final-test outcomes were unavailable.
- The candidate meets the pre-test calibration, support, and latency requirements.
- Every fixed deadline-probability band below 0.95 that can emit a numeric recommendation has its pre-test complete-population calibration and support artifact, including bands below 0.80 used by `TARGET_NOT_MET` fallback.
- The final stable output-support eligibility manifest and complete fixed-point discovery-artifact hash covering deadline bands, deadline slices, target-specific cells, transfer candidate-population deciles, transfer stations, selected-policy trigger deciles, and quantile levels are frozen into the decision-policy hash before final-test outcomes become accessible.
- The simplest candidate that passes the predeclared rule is promoted.
- `make gate MILESTONE=4` passes.

### Milestone 5 - Decision, recovery, and service API

Deliverables:

- Write the asset, sensitive-data, trust-boundary, attacker, abuse-case, and authorization threat model before implementing the externally reachable API paths.
- Integrate the already frozen side-effect-free initial decision kernel into historical replay, live scoring, and the API without a second selection implementation.
- Implement the exact recovery-reason precedence, candidate universe, cap-reference and continuation-comparator semantics, deterministic schedule-only ranking and backup rules, model-output suppression, recovery-decision binding, and trip-state transition graph.
- Implement versioned decision contexts that apply causally available alert masks without mutating the frozen static candidate universe.
- Implement deterministic explanations and the recovery state machine.
- Implement the V1 live-update contract that freezes the initial CDF estimate, limits post-start SSE values to deterministic or independently supported state-conditioned outputs, and records event cutoff, lineage, freshness, and value provenance.
- Implement API and SSE contracts with event kind, value provenance, model, cutoff, feature-schema, static candidate-manifest, decision-context, support, and feed-status metadata.
- Implement the server-owned initial cutoff, visible past-ready normalization, conservative deadline-grid normalization, exact zero-through-15-minute model-support domain, informational future-ready schedule-only fallback, and non-trip-startable null-decision response.
- Implement server-owned single-use 256-bit decision capabilities with versioned keyed digest storage, memory-only browser handling, ten-minute expiry, exact-Origin consumption, non-cacheable responses, and complete observability redaction.
- Implement nonsecret trip identifiers, one-time 256-bit trip-bearer creation, versioned keyed digest storage, constant-time authorization on every trip endpoint, authenticated fetch-stream SSE, six-hour deletion, and no-store responses.
- Implement validated idempotent optimistic state transitions with atomic SSE outbox behavior.
- Implement HTTPS enforcement for non-loopback use, trusted-proxy restrictions, exact Host and Origin validation, no wildcard CORS, CSP and browser security headers, request validation and size limits, initial rate limits, bounded SSE retention, and sensitive log, trace, metric, exception, and frontend-report redaction.
- Implement operational metrics and bounded queues.

Acceptance gate:

- Recorded direct, transfer, no-target, unsupported-target, degraded, stale, fallback, and recovery scenarios return deterministic decisions.
- Historical replay and the API produce byte-identical comparator, cap-eligible set, recommendation, target status, and backup outputs for identical frozen inputs.
- Only the recommended response slot may expose selected-policy deadline probability and quantiles, while fastest, backup, and alternative slots reproduce null model outputs and `NOT_SELECTED_OUTPUT_UNVALIDATED` in replay and the API.
- Historical replay and the API also produce byte-identical recovery reason, cap reference, continuation comparator, distinct selectable set, schedule-derived recommendation, recovery status, backup, null model outputs, and state transition for identical frozen recovery inputs.
- Every initial search uses one immutable server-owned cutoff, never sees a later-arriving source record, and follows the exact ready-time normalization, deadline normalization, and horizon fallback branches.
- Every post-start event follows the frozen live-update allow-list, and no later source record invokes the initial CDF or changes its original deadline probability or quantiles.
- Closure updates invalidate the selected policy deterministically, official Trip Update annotations remain visibly separate, and only the supported conditional transfer path may emit a new state-conditioned model value.
- No deadline probability is emitted without a coherent CDF bundle, support status, data cutoff, and feed status.
- No probability below 0.80 is emitted unless the static initial ready-horizon rule, its exact fixed band, and every applicable slice pass the frozen gates.
- No numeric transfer probability is emitted without its passing candidate-population decile and transfer-station artifacts, and no probability-based recovery trigger is emitted without its additional selected-policy decile artifact.
- Every recovery payload has null new deadline probabilities and quantiles, omits reliability-target status, and reproduces the frozen schedule-only action tuple without invoking the arrival-time CDF.
- Recovery guidance is conditional until the rider or oracle state is confirmed.
- The Milestone 5 threat model has no unresolved critical or high-severity finding for the implemented local API and browser flow.
- Decision-capability leakage, storage, cache, expiry, replay, concurrent consumption, cross-site consumption, and constant-shape failure tests pass.
- A fastest, backup, or alternative itinerary identifier cannot consume a decision capability or create a trip, and the exact recommended identifier can be consumed at most once.
- Unauthorized trip access, forged decision ownership, invalid or concurrent state transition, bearer replay after stop or expiry, hostile Origin, untrusted forwarded header, plaintext non-loopback use, browser-header, script-injection, oversized input, rate-limit, SSE-authorization, and observability-redaction tests pass.
- The frozen p95 latency targets pass on the named hardware and workload without post-result revision.
- `make gate MILESTONE=5` passes.

### Milestone 6 - Frozen offline decision evaluation

Deliverables:

- Freeze queries, candidates, models, calibration, support, policy thresholds, evaluation code, and all manifest hashes before opening final-test outcomes.
- Verify the stable output-support eligibility manifest and complete fixed-point discovery-artifact hash from a fresh process, then freeze both into the decision-policy hash before opening final-test outcomes.
- Freeze the transfer classifier, transfer calibrator, fixed deciles, station support, 0.50 recovery threshold, complete-population transfer-calibration formulas, and pre-test eligibility of every transfer candidate-population decile, transfer station, and selected-policy trigger decile before opening final-test outcomes.
- Freeze the exposed quantile levels, quantile support and coverage formulas, and pre-test eligibility of every quantile level before opening final-test outcomes.
- Freeze the 0.90 and 20-minute primary contrast, static-fastest comparator, complete-population bound formulas, secondary hypothesis family, and Holm ordering before opening final-test outcomes.
- Freeze the deterministic recovery candidate universe, distinct-action filter, cap-reference rule, schedule ranking and backup tuples, null model-output contract, and secondary evaluation before opening final-test outcomes.
- Run predictive, initial selected-policy, subgroup, disruption, recovery, and censoring-bound comparisons.
- Run paired complete-service-day bootstrap intervals.
- Run API, candidate, and replay benchmarks.
- Publish immutable machine-readable reports and cards.

Acceptance gate:

- The Section 6.1 primary worst-case-bound improvement, calibration, added-time, coverage, and performance gates pass.
- Every deadline band, deadline slice, and target-specific cell frozen as eligible before final-test access passes its applicable final-test complete-population calibration, count, service-day, slice, and target gates.
- Every transfer candidate-population decile, transfer station, selected-policy trigger decile, and quantile level frozen as eligible before final-test access passes its applicable final-test support, calibration, count, service-day, and coverage gates.
- A final-test failure of any output-support cell frozen as eligible fails this policy version, remains visible in its immutable report, and cannot be converted into a passing result by suppressing the failed cell after outcomes are opened.
- Any revised eligibility manifest belongs to a new acceptance version and must pass on an untouched future test interval before the revised policy can be promoted.
- Every displayed transfer-probability candidate decile and enabled transfer station was frozen eligible before final-test access and passes its complete-population support and calibration gates, and every probability-based recovery trigger was frozen eligible and also passes its selected-policy decile gate.
- Transfer cells, trigger cells, and quantile levels frozen as pre-test-ineligible remain null, disabled, or omitted as applicable, and an ineligible trigger cell cannot trigger probability-based recovery.
- Negative results, uncertainty, availability, and all censoring bounds are included.
- The main result is a reliability-time Pareto curve on the initial selected policy.
- Every rerun is versioned and the original frozen result is never replaced.
- If the learned-policy gate fails but the frozen model-free bundle passes every primary gate, publish only the explicitly model-free recommendation result.
- If neither bundle passes, execute the historical-explorer pivot and remove live recommendation claims.
- `make gate MILESTONE=6` passes.

### Milestone 7 - Rider product and offline-validated local portfolio release

Deliverables:

- Build station, time, deadline, target, and extra-time inputs.
- Show requested and effective deadlines plus the conservative grid-normalization limitation whenever they differ.
- Build fastest and safer cards, transfer and backup timelines, and accessible CDF-derived uncertainty displays.
- Implement trip sessions, explicit state confirmation, provenance-labeled deterministic SSE updates, supported conditional transfer estimates, and conditional recovery.
- Add methodology, attribution, evidence status, competitor matrix, and limitations views near probability displays.
- Run the eight-participant comprehension protocol with participants who did not build the product or see an earlier evaluated interface.
- Produce a retained historical replay demonstration.

Acceptance gate:

- Browser tests exercise complete direct, transfer, recovery, degraded, unsupported-target, and stale-feed paths.
- Browser tests also exercise below-0.80 supported and suppressed fallback, visible past-ready and deadline normalization, and the future-ready schedule-only branch with trip start disabled.
- Browser tests verify that only the recommended card can display a deadline probability or quantile and that every non-recommended card visibly labels those model outputs unavailable.
- The recovery browser path shows the schedule-action label, null new deadline probability and quantiles, no reliability-target status, and the exact distinct recommendation and backup returned by the server.
- At least seven of eight independent participants pass the guarantee and `TARGET_NOT_MET` comprehension checks.
- If one comprehension-driven revision is needed, the repeated gate uses eight fresh participants.
- The interface remains usable without a map and without color-only distinctions.
- Every offline claim links to an immutable Milestone 6 artifact.
- The product labels prospective live calibration as pending.
- The application remains loopback-only and the release instructions forbid non-loopback deployment before Milestone 9.
- `make gate MILESTONE=7` passes.

This is the first portfolio-ready checkpoint, and it supports only the frozen offline claims that passed Milestone 6.

### Milestone 8 - Prospective collector shakeout and frozen shadow evaluation

Deliverables:

- Run the collector for a 28-service-day operational shakeout and fix operational defects before freezing the panel.
- Freeze and timestamp the prospective panel, `historical_v1` bundle, candidate configuration, freshness rules, support policy, and precision target.
- Collect at least 56 additional service days of immutable snapshots and planned decisions.
- Resolve outcomes only after each journey completes.
- Compare prospective and historical calibration, decisions, availability, censoring bounds, freshness, latency, and failures.
- Report the nonserving 0.95 shadow policy separately from the serving policy and preserve its provisional-cell status in every artifact.

Acceptance gate:

- Every scheduled panel query appears in end-to-end availability, including collector and router failures.
- Every retained decision is reproducible from exact feed blobs, fetch attempts, candidate manifest, feature row, and model bundle.
- The panel receives `PASSED` only when it meets the serving-policy count, target-band support, nonserving 0.95 shadow-evidence, and precision rules in Sections 6.1 and 23.
- Milestone 8 passing never enables 0.95 in the current serving policy and only authorizes its immutable panel as pre-test evidence for a new acceptance version.
- A panel that misses one of those rules receives `INSUFFICIENT_EVIDENCE`, preserves and publishes its negative evidence report, and does not pass `make gate MILESTONE=8`.
- Prospective and historical results remain separate.
- `prospective_v2` is not trained on the frozen shadow panel.
- Only a `PASSED` `make gate MILESTONE=8` artifact unlocks a prospective calibration claim for the serving policy, while the nonserving 0.95 result remains shadow evidence and never a public reliability claim.

### Milestone 9 - Reliability, security, and publication package

Deliverables:

- Update the Milestone 5 threat model for the intended non-loopback topology, audit and harden the rate limits, resource bounds, authorization, sensitive-log redaction, and session expiration, then add backup and restore procedures.
- Add dependency, container, secret, and static scans.
- Add source-outage, model-load, router, database, SSE, and clock-skew fault tests.
- Finish README, architecture diagram, operations guide, model card, data card, security document, evaluation report, and reproduction guide.
- Verify a clean checkout in a second fresh environment using only documented commands and permitted external data downloads.
- Audit licenses, attribution, secrets, generated artifacts, stale claims, and untracked source.

Acceptance gate:

- Failure states are safe and understandable.
- Critical and high-severity security findings are resolved.
- A clean deployment recovers from each seeded failure.
- No rider identity or coordinates are retained, and trip state expires within six hours.
- Every public claim maps to an immutable artifact and its acceptance-version hash.
- A non-loopback deployment is permitted only after the release threat model has no unresolved critical or high-severity finding and the scans, proxy and TLS configuration, browser security headers, authorization tests, and seeded fault tests all pass.
- The repository is clean and all required source is tracked.
- Publication waits for explicit authorization.

### Milestone 10 - Extensions after release

Eligible extensions are:

- `prospective_v2` with Trip Update prediction features and its own untouched test panel.
- Bus or commuter-rail support after a new data audit.
- Two-transfer itineraries.
- Door-to-door planning.
- Learned walking distributions.
- Additional agencies.
- Privacy-preserving personal calibration.
- Native notifications.
- Accessibility-aware disruption routing with validated data.

Each extension requires a new causal data contract, acceptance version, slice evaluation, and scope statement.

## 32. Commit discipline

Every milestone ends in a focused commit only after its acceptance gate passes.
Large milestones use submilestone commits for data contracts, ingestion, replay, models, API, UI, and evaluation.
Each commit leaves formatting, linting, typing, unit tests, and relevant integration tests passing.
Do not commit large raw feeds, private location data, model caches, secrets, or mutable benchmark outputs.
Do not push, deploy, or publish without explicit authorization.

## 33. Kill gates

Stop or pivot the central approach if:

- Neither direct Vehicle Position station events nor conservative next-stop Vehicle Position upper-bound events can meet the frozen audit-candidate resolution and censoring gates after a truthful scope reduction.
- Observed stop-presence evidence cannot meet the same audit-candidate resolution gates, even when downstream destination evidence is abundant.
- Public source fields cannot distinguish either permitted Vehicle Position evidence mode from predictive Trip Update fallback.
- Historical features cannot be separated from future events.
- No historical operational feature family passes its provenance and support gate and the project is unwilling to publish an explicitly schedule-only V1 or wait for prospective training data.
- Planned-trip matching ambiguity causes a required coverage or censoring gate to fail.
- Neither a calibrated interval-censored learned model nor a truthful model-free distribution passes the frozen reliability-time improvement rule over the static-fastest comparator.
- Selected-policy calibration fails the 0.05 target-band gap rule.
- OpenTripPlanner cannot produce deterministic candidates or pass the 99 percent audit-recall gate under a pinned graph.
- The live collector cannot retain exact snapshots and scheduled decision attempts with reproducible lineage.
- Fewer than seven of eight fresh usability participants pass the guarantee and target-not-met checks after the one permitted design iteration.

A final-test calibration, support, or coverage failure for any deadline band, deadline slice, target-specific cell, transfer candidate-population decile, transfer station, selected-policy trigger decile, or quantile level frozen as eligible invalidates that policy version and requires a new acceptance version with an untouched future test interval rather than post-test suppression.
A nonserving shadow panel may supply pre-test evidence to that new version but can never serve simultaneously as the untouched final test that promotes its newly eligible cells.
A valid pivot is a historical MBTA reliability explorer with no live recommendation claim.
An invalid pivot is random splitting, future leakage, or replacing decision evidence with a prettier map.

## 34. Major risks and mitigations

### Historical prediction absence

Use only causally available historical operational events and collect prospective snapshots.
Never imply exact historical rider-visible prediction comparisons without source snapshots.
Keep Trip Update prediction as a baseline until `prospective_v2` has independent chronological evidence.

### Label provenance ambiguity

Use one frozen Vehicle Position evidence mode for primary arrival intervals and retain Trip Update fallback only as a separate sensitivity track.
Treat the first `STOPPED_AT` observation and conservative station-departure timestamp as upper bounds, pair them with defensible lower bounds, and reflect interval uncertainty in every claim.
Pivot if the public export cannot preserve either permitted distinction at the required coverage.

### Correlated delays

Train on whole candidate-policy outcomes and use paired complete-service-day bootstrap intervals.
Do not multiply marginal leg probabilities.

### Rare disruption events

Report wide intervals, pool only under documented regimes, and abstain when support is sparse.

### Feed staleness

Track source and fetch timestamps, degrade visibly, and never reuse expired values as live.

### Trip identity ambiguity

Preserve exact, ambiguous, unmatched, and canceled status.
Report coverage, censoring bounds, and fallback sensitivity without silently forcing a schedule match.

### Walking-time uncertainty

Use conservative fixed transfer times and disclose them.
Do not claim personal accessibility or walking accuracy.

### Existing mature planners

Publish a dated competitor matrix and keep the product centered on auditable calibrated deadline risk, explicit target tradeoffs, and recovery evaluation.
Do not claim invention of reliability-aware routing.

### Model complexity pressure

Require a measured benefit before adding sequence models, neural networks, or GPUs.

## 35. Public evidence artifacts

The final repository includes:

- Data-source and license manifest.
- Acceptance charter and machine-readable milestone gate reports.
- Contiguous historical query-population manifest with aggregate scope exclusions and retained outage mass.
- Primary-label provenance and source-quality report.
- Arrival-interval width, source-observation-to-product-availability lag, and evidence-time-versus-model-error limitations report.
- Temporal-semantics document.
- Leakage-test suite with seeded failures.
- Baseline comparison table.
- Calibration diagrams.
- Quantile coverage table.
- Interval-identified quantile coverage bounds and conditional pinball-label disclosure.
- Reliability-time Pareto curves.
- Decision and subgroup metrics with confidence intervals.
- Outcome-resolution, censoring-reason, and best-case and worst-case bound reports.
- Complete-band initial selected-policy calibration bounds with resolved and unresolved mass.
- Frozen pre-test output-support eligibility manifest plus immutable final-test pass or failure results for every eligible deadline band, deadline slice, target-specific cell, transfer candidate-population decile, transfer station, selected-policy trigger decile, and quantile level.
- Static-candidate and alert-derived decision-context parity report.
- Historical and prospective reports kept separate.
- Feed-freshness and failure report.
- API latency benchmark.
- Data card.
- Model card.
- Recorded product demonstration.
- Dated competitor matrix.
- Usability comprehension protocol and aggregate result with no participant identity.
- Reproduction commands and environment manifests.

## 36. Authoritative references

- [MBTA LAMP public data](https://performancedata.mbta.com/)
- [MBTA LAMP data dictionary](https://github.com/mbta/lamp/blob/main/Data_Dictionary.md)
- [MBTA LAMP Performance Manager semantics](https://github.com/mbta/lamp/blob/main/src/lamp_py/performance_manager/README.md)
- [MBTA V3 API documentation](https://api-v3.mbta.com/docs/swagger)
- [GTFS overview and schedule reference](https://gtfs.org/documentation/overview/)
- [GTFS Realtime reference](https://gtfs.org/documentation/realtime/reference/)
- [GTFS Realtime Vehicle Positions semantics](https://gtfs.org/documentation/realtime/feed-entities/vehicle-positions/)
- [XGBoost accelerated failure time survival analysis](https://xgboost.readthedocs.io/en/latest/tutorials/aft_survival_analysis.html)
- [MassDOT and MBTA developer resources](https://www.mass.gov/lists/mbta-and-transit-data-for-developers)
- [MassDOT developer license](https://www.mass.gov/doc/developers-license-agreement-11132009/download)
- [OpenTripPlanner](https://github.com/opentripplanner/OpenTripPlanner)
- [OpenTripPlanner APIs](https://docs.opentripplanner.org/en/latest/apis/Apis/)
- [Chance-constrained reliable trip planning](https://doi.org/10.1007/s12469-016-0134-y)
- [Online routing with transfer-failure probability](https://doi.org/10.1016/j.trb.2019.04.009)
- [PROTRIP](https://pmc.ncbi.nlm.nih.gov/articles/PMC7840083/)
- [Transit routing with backup itineraries](https://doi.org/10.1016/j.ejor.2021.08.029)

## 37. Representative official role references

- [SentiLink Research Scientist, New Grad](https://jobs.ashbyhq.com/sentilink/f9a47314-c48a-4053-a113-6974b211559f/application?embed=true)
- [TikTok Machine Learning Engineer, Performance Monetization](https://lifeattiktok.com/search/7669691374918011141)
- [TikTok Machine Learning Engineer, Search Ads](https://lifeattiktok.com/search/7669698543896054069)
- [TikTok Machine Learning Engineer, Visual Search](https://lifeattiktok.com/search/7667346535273007413)
- [Quora Software Engineer, Machine Learning Platform](https://jobs.ashbyhq.com/quora/452afc2e-0c79-41f8-8201-1aab7df775db/application?embed=true)
- [CrowdStrike Data Scientist, New Grad](https://crowdstrike.wd5.myworkdayjobs.com/crowdstrikecareers/job/USA---Sunnyvale-CA/Engineer-I--Data-Scientist---New-Grad--Hybrid-_R29382-1)
- [Preference Model Member of Technical Staff, ML Capabilities](https://jobs.ashbyhq.com/Preference-Model/44642065-e592-44ba-810d-a019703463b6/application)
- [Snowflake AI Research Scientist, Agents and Reinforcement Learning](https://jobs.ashbyhq.com/snowflake/1bad12df-f443-426f-9d09-e96fc780d698/application)
- [NVIDIA Research Scientist, Efficient Deep Learning](https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/US-CA-Santa-Clara/Research-Scientist--Efficient-Deep-Learning---New-College-Grad-2026_JR2019729-1)
- [NVIDIA Deep Learning Software Engineer, TensorRT Performance](https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/US-CA-Santa-Clara/Deep-Learning-Software-Engineer--TensorRT-Performance---New-College-Grad-2026_JR2015071-1)
- [Figma Data Scientist, Core Data](https://job-boards.greenhouse.io/figma/jobs/5976930004?gh_jid=5976930004)
- [Parallel Research Engineer](https://jobs.ashbyhq.com/parallel/056e41f8-7d5f-41c1-99fd-bf002dc072fd/application)

Role pages can close after the planning snapshot.
The build remains aligned to the recurring responsibilities, not to one employer's exact stack.

## 38. Final positioning

The strongest interview story is:

> I built a transit ML product where every training feature obeys event time and product-availability time, Vehicle Position observations become interval-censored outcomes rather than fabricated exact arrivals, deadline probabilities and quantiles come from one calibrated latent-arrival CDF, and the final metric tests whether the selected route policy improves deadline arrival without unreasonable extra travel time.

That story is credible only if the repository includes label-provenance evidence, the temporal access boundary, frozen candidates, prospective snapshots, baseline comparisons, initial selected-policy calibration, censoring bounds, decision metrics, and honest failure states required by this plan.
