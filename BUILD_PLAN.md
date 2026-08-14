# Arrive90 Build Plan

Status: implementation-ready plan.

Planning snapshot: 2026-08-14.

Acceptance version: `travel-time-v1.1`.

The predictive artifact family remains `travel-time-v1`.

Arrive90 is a local-first MBTA rail travel-time reliability lab.
It estimates a calibrated distribution for how long an already observed train will take to be observed stopped at a selected downstream station.
It combines a reproducible historical data pipeline, interval-censored modeling, chronological evaluation, and a read-only browser explorer.

## 1. Product thesis

Arrive90 answers one precise question:

> Given a train observed at an origin stop, how long until that same train is next observed stopped at a selected downstream stop, and what is the estimated probability that this happens within a chosen horizon?

The contribution is an end-to-end ML and data product built from public MBTA observations.
The strongest technical signals are:

- Reproducible acquisition and normalization of a complete year of daily Parquet objects.
- Deterministic deduplication and trip-episode construction across overlapping source objects.
- Interval-censored and right-censored travel-time labels derived from minute-cadence VehiclePosition observations.
- Strict observation-cutoff feature generation with seeded leakage failures.
- Chronological model selection, calibration, and untouched final evaluation.
- One coherent arrival-time CDF that supplies probabilities and quantiles.
- Service-day block-bootstrap uncertainty and detailed reliability slices.
- Immutable source, dataset, model, and evaluation lineage.
- A polished local replay and reliability explorer backed by real held-out examples.

The project optimizes for credible ML engineering and a strong interview demonstration.
It does not require a public production deployment.

## 2. Target user and primary demonstration

The primary user is a recruiter, interviewer, ML engineer, data engineer, or transit analyst evaluating the project locally.

The primary workflow is:

1. Install the pinned environment from a clean checkout.
2. Run the small pinned-day demonstration workflow or the complete 2024 reproduction workflow.
3. Open the local Arrive90 explorer.
4. Select a line, direction, observed origin stop, downstream destination, and held-out replay case.
5. Inspect the schedule, empirical-midpoint diagnostic, promoted model distribution, median, p80, p90, and horizon probabilities.
6. Reveal the later observed arrival interval for that replay case.
7. Compare the prediction with the observed interval and the baselines.
8. Open aggregate calibration, error, slice, drift, and data-lineage views.

The browser must distinguish information available at the replay cutoff from the later observation used only for evaluation.
The browser must expose the model version, feature cutoff, source manifest, split, and result provenance for every replay.

## 3. Strict V1 scope

### 3.1 Supported

- One agency: MBTA.
- Rail VehiclePosition observations from calendar year 2024.
- The heavy-rail route identifiers `Red`, `Orange`, and `Blue`, subject to the frozen line-retention gate.
- A train already observed at a specific stop.
- A downstream stop on the same matched trip pattern.
- Historical observation replay.
- Conditional time-to-downstream-stop distributions.
- Probabilities for the fixed 5, 10, 15, 20, 30, 45, and 60 minute horizons.
- Median, p80, and p90 time-to-stop estimates when supported inside the 60-minute model horizon.
- Static-schedule and empirical-midpoint diagnostics plus comparable interval-censored AFT baselines and candidates.
- A read-only local API and browser explorer.
- Small committed derived fixtures and aggregate reports permitted by the source terms.

### 3.2 Explicit non-goals

- Live rider recommendations.
- Boarding, door-state, cancellation, or first-available-train claims.
- Multi-leg routing, transfers, recovery itineraries, or OpenTripPlanner.
- Door-to-door travel time.
- Bus or commuter-rail modeling.
- Green Line branch and Mattapan Line modeling.
- User accounts, rider tracking, notifications, or mutable trip sessions.
- Cloud deployment, Kubernetes, Kafka, or a distributed feature store.
- GPU training.
- LLM features or explanations.
- Causal claims about service interventions.
- Generalization beyond the retained 2024 MBTA rail scope.

## 4. Authoritative data sources

### 4.1 Bus Observatory MBTA archive

The source inventory is published at:

`https://busobservatory-lake.s3.amazonaws.com/index/data-inventory.json`

The calendar interface is published at:

`https://api.busobservatory.org/`

The inventory generated on 2026-08-14 contains one `mbta_all` compacted Parquet object for every date from 2024-01-01 through 2024-12-31.
The core calendar-year interval therefore contains 366 objects and approximately 7,294.22 MB of compressed Parquet data.
Acquisition also locks the 2023-12-31 and 2025-01-01 boundary objects so trips with `trip.start_date` in 2024 are not truncated by a compaction boundary.

Each source object represents approximately 24 hours of minute-cadence collection.
Object boundaries overlap and do not correspond exactly to calendar-day boundaries.
Normalization must use observation fields and trip service dates rather than treating object names as service dates.

The inspected 2024-05-15 object is:

`https://busobservatory-lake.s3.amazonaws.com/feeds/mbta_all/COMPACTED_mbta_all_2024-05-15_13:42:26.parquet`

Its pinned local qualification values are:

- Size: 23,432,007 bytes.
- SHA-256: `e91537e12d7cb68fd06d467e70e33a8cda02c682102098a1ce9baad7692eac73`.
- Rows: 610,834.
- Whole-object source observation range: 2024-05-14 13:30:54 through 2024-05-15 13:40:36 in timezone-naive UTC.
- Retained Red, Orange, and Blue observation maximum: 2024-05-15 13:40:34 in timezone-naive UTC.

The required source fields are:

- Entity identifier.
- Trip identifier, start date, start time, route identifier, direction, and schedule relationship.
- Vehicle identifier and label.
- Vehicle timestamp.
- Current stop sequence, stop identifier, and current status.
- Latitude, longitude, bearing, and speed.

The public compacted files do not retain collector fetch time or FeedHeader timestamp.
The V1 estimand is therefore explicitly conditional on a historical vehicle observation timestamp.
V1 makes no claim that the compacted observation was rider-visible at that historical instant.

### 4.2 Official MBTA 2024 schedule archive

The schedule source is:

`https://performancedata.mbta.com/lamp/gtfs_archive/2024/GTFS_ARCHIVE.db.gz`

The source returned HTTP 200 on the planning snapshot with:

- Content length: 150,725,341 bytes.
- Last modified: 2025-01-01 12:04:22 UTC.
- ETag: `1b0ca9ccff025f116939faadefccaafd-18`.

The complete downloaded SHA-256 and expanded SQLite SHA-256 are recorded by Milestone 0 before the pinned-day schedule match.
Milestone 1 builds and verifies the complete-year read-only lookup index from that content lock.
The archive provides the schedule versions, routes, trips, stop sequences, service calendars, and planned stop times needed for matching and schedule baselines.

### 4.3 License and artifact policy

Bus Observatory data is published under CC BY-NC 4.0 with attribution to the Jacobs Urban Tech Hub at Cornell Tech.
The official schedule data retains its applicable MassDOT and MBTA attribution.

Raw archives, normalized full-year partitions, caches, and local profiler outputs remain ignored.
Full local training outputs remain ignored except for one explicitly allow-listed promoted predictive-distribution bundle no larger than 10 MiB under `data/demo/travel-time-v1/model/`.
The repository may retain source locks, hashes, schemas, aggregate metrics, the allow-listed model bundle, small derived fixtures, cards, and screenshots with the required attribution.

## 5. Frozen source and observation contracts

### 5.1 Inventory and acquired-content entries

```text
InventoryLockEntry
  inventory_snapshot_url
  inventory_snapshot_sha256
  inventory_generated_at
  inventory_date
  source_object_key
  source_url
  declared_size_mb

AcquisitionContentEntry
  source_object_key
  source_url
  response_size_bytes
  etag
  last_modified_at_utc
  downloaded_at_utc
  sha256
  schema_fingerprint
  row_count
  parser_version

DerivedArtifactEntry
  artifact_id
  source_content_sha256
  transformation_name
  transformation_version
  transformation_parameters
  output_size_bytes
  output_sha256
  schema_fingerprint
```

The exact downloaded inventory snapshot is committed under a content-addressed source-snapshot path, and its SHA-256 must equal every derived lock entry's `inventory_snapshot_sha256`.
The canonical 2024 inventory lock is extracted from that committed snapshot, sorted by ISO date and bytewise object key, and stored as a small committed lock before bulk acquisition.
The inventory lock contains exactly the 366 calendar entries plus the two boundary entries and does not claim hashes or exact byte sizes that the inventory does not publish.
The acquired-content lock records observed response metadata and content facts only after a successful verified download.
The derived-artifact lock records deterministic transformations of acquired bytes and never overloads HTTP response fields.
The expanded schedule database entry binds the compressed archive SHA-256, standard-library gzip expansion, Python runtime version, empty parameter map, expanded byte size, expanded SHA-256, and SQLite schema fingerprint.
The SQLite schema fingerprint hashes canonical JSON of every noninternal `sqlite_master` table, index, view, and trigger tuple `(type, name, tbl_name, sql)` sorted by UTF-8 bytes, with SQL null retained as JSON null.
Milestone 0 commits the complete inventory lock plus acquired-content entries for the pinned sample and official schedule archive.
Milestone 0 also commits the expanded schedule database's `DerivedArtifactEntry` while the expanded database itself remains ignored.
Milestone 1 commits acquired-content entries for all 368 Bus Observatory objects.
The downloader resumes partial files, verifies response length, computes SHA-256 while streaming, and never overwrites a verified object with different bytes.

### 5.2 Normalized vehicle observation

```text
VehicleObservation
  observation_id
  source_lineage
    source_object_key
    source_row_ordinal
  entity_id
  trip_id
  trip_start_date
  trip_start_time
  schedule_relationship
  route_id
  direction_id
  vehicle_id
  vehicle_label
  observation_source_naive_utc
  observation_utc
  stop_sequence
  stop_id
  current_status
  latitude
  longitude
  bearing
  speed
  schema_version
```

`source_lineage` is a nonempty ordered collection of exact `(source_object_key, source_row_ordinal)` entries.
The normalized contract has no singular source projection, so no duplicate row ordinal can be lost or mistaken for the complete lineage.
The canonical observation identity key is the ordered tuple `(trip_start_date, trip_start_time, trip_id, route_id, direction_id, vehicle_id, observation_utc, stop_sequence, current_status)`.
The compared state payload is `(entity_id, schedule_relationship, stop_id, vehicle_label, latitude, longitude, bearing, speed)`.
Known enums are normalized to their canonical uppercase string values and unknown integral or string values are rejected.
Null remains explicit JSON null.
Finite floats are represented for hashing by their exact IEEE-754 hexadecimal value after normalizing signed zero to positive zero, and NaN or infinity is rejected.
Lineage pairs `(source_object_key, source_row_ordinal)` are sorted by UTF-8 object-key bytes and then integer ordinal.
Rows with the same identity key and byte-identical canonical payload collapse to one observation while retaining every sorted lineage pair.
Rows with the same identity key and different payloads are all quarantined as `CONFLICTING_DUPLICATE_STATE` rather than selected by file order.
The `observation_id` is the SHA-256 of the canonical identity key after source UTC attachment and payload conflicts are eliminated.
An event evidence key omits `stop_sequence` and `current_status` from the identity key.
Multiple sequences under one event evidence key are ambiguous, while multiple statuses at one sequence remain an evidence set for target-bound rules.

### 5.3 Source timestamp rule

The locked 2024 MBTA compacted objects store `vehicle.timestamp` as timezone-naive UTC.
Normalization requires a naive source value and attaches UTC without clock arithmetic before canonical deduplication and episode construction.
Boston local time is derived only after that attachment for schedule and calendar features.

The frozen discriminator uses exact platform-and-sequence `STOPPED_AT` matches against the official schedule.
On the pinned object, 13,260 matched observations have median schedule deviation -88 seconds under naive-UTC interpretation and 14,312 seconds under naive-Boston interpretation.
The source profile records these values and the archived producer implementation used to define the field.

The frozen order is raw validation, source UTC attachment, canonical deduplication, and then trip-episode construction.
Raw-lineage order may be audited for regressions but cannot change the canonical timestamp or inspect schedule matches, outcome status, destination rows, or model features.

### 5.4 Trip episode

```text
TripEpisode
  episode_id
  service_date
  trip_id
  trip_start_time
  route_id
  direction_id
  vehicle_id
  first_observation_utc
  last_observation_utc
  observation_ids
  maximum_gap_seconds
  schedule_match_status
  schedule_version_id or null
  route_pattern_id or null
  quality_flags
```

An episode never crosses service date, trip identifier, trip start time, route, direction, or vehicle identity.
After deduplication, observations under that session key are ordered by `(observation_utc, stop_sequence with null last, current_status as canonical UTF-8 bytes, observation_id)`.
All observations at one UTC timestamp form one event-evidence group before any episode boundary is evaluated.
An event group with more than one distinct nonnull stop sequence is flagged `AMBIGUOUS_STOP_SEQUENCE` and cannot anchor, bound an outcome, or update the monotone sequence cursor.
Null-only sequence groups cannot anchor, bound an outcome, or update that cursor.
An episode is split before an event group when its UTC gap from the prior group exceeds 600 seconds or its single nonnull source stop sequence is lower than the most recent prior single nonnull source sequence.
The latter boundary receives `STOP_SEQUENCE_REGRESSION`, and any later recovery progresses only inside the new episode.
Raw-lineage timestamp regressions are reported during provisional normalization, but ingestion order never determines canonical episode ordering.
Conflicting statuses at one timestamp and sequence remain an evidence set in which `STOPPED_AT` may supply an upper bound but no same-timestamp status may supply the strictly earlier lower bound.
The 600-second boundary is frozen from the one-day cadence audit before full-year episode construction.
Sensitivity reports compare 300-second and 900-second boundaries without changing the primary episode definition.

### 5.5 Schedule version and exact match

For each `trip_start_date`, the schedule selector reads the expanded archive in SQLite read-only mode and requires exactly one `feed_info` row whose inclusive `gtfs_active_date` and `gtfs_end_date` contain that date.
The selector parses the UTC publication timestamp embedded in `feed_version`; an unparseable value is a schema failure.
The active version is usable by an episode only when its publication timestamp is no later than the episode's first source observation UTC, and it is usable by a feature row only when that timestamp is no later than the anchor cutoff.
Zero active rows or a version published after the cutoff produce `SCHEDULE_UNMATCHED`, and multiple active rows produce `SCHEDULE_VERSION_CONFLICT`; none of these cases is resolved by row order.
The `schedule_version_id` is the SHA-256 of canonical JSON containing the expanded database SHA-256, `feed_version`, publication timestamp, active start, and active end.
An episode matches only when trip identifier, route identifier, direction, trip start date and time, platform stop identifier, and stop sequence all agree with that active version.
Parent-station identifiers are display metadata and never substitute for an exact platform match.
All `24:00:00`-plus GTFS times are interpreted as offsets from the selected service date before conversion to Boston local time and UTC.
The schedule selector, match result, and query index are deterministic under the expanded-database derived-artifact hash.

## 6. Prediction target and example generation

### 6.1 Prediction anchor

A prediction anchor is the first canonical `STOPPED_AT` observation for one stop sequence in a trip episode.
Its `observation_utc` is the example cutoff.

Every feature and schedule version used by the example must be selected without reading a later VehicleObservation.
Outcome construction runs in a separate package and joins only after the feature row is finalized.

### 6.2 Destination selection

The matched schedule pattern defines downstream destinations.
For each anchor, V1 selects every one of the next eight downstream scheduled stop sequences whose scheduled remaining duration is positive and no greater than 1,800 seconds.
Destinations beyond the route pattern or duration limit are ignored.

This rule is fixed before outcome construction.
It bounds dataset size while covering immediate, medium, and long within-line horizons.

### 6.3 Arrival interval

For a selected downstream destination, the arrival upper bound is the first later canonical `STOPPED_AT` observation for that destination stop sequence in the same episode.
The arrival lower bound is the latest earlier same-episode observation that identifies the train as not yet stopped at that destination.
The origin anchor itself is a valid lower-bound observation because it records the train stopped at an earlier scheduled sequence.

The latent downstream stop-observation event lies in the interval `(lower, upper]`.
The AFT label subtracts the anchor time from both bounds.
When the anchor is the only valid lower evidence, the example is represented as left-censored `[0, upper]` and receives outcome state `LEFT_CENSORED`.

A finite interval is valid only when:

- The lower observation occurs at or after the anchor.
- The upper observation follows the lower observation.
- The upper observation belongs to the same episode and destination sequence.
- The lower bound is nonnegative and the upper bound is positive after subtracting the anchor.
- The interval width is no greater than the retained-line quality limit.

An over-width interval receives outcome state `OVER_WIDTH_INTERVAL`, remains in coverage and interval-width quality denominators, and is excluded from AFT fitting and predictive metric denominators.
A finite example with a strictly positive lower bound receives outcome state `INTERVAL_RESOLVED`.

### 6.4 Right censoring

When the destination event is not observed and continuous coverage ends before the destination sequence, the example is right-censored at the end of that coverage, capped at 60 minutes after the anchor.
The right-censored row uses that positive coverage duration as its lower bound and positive infinity as its upper bound.

When a later scheduled sequence is observed without a destination `STOPPED_AT` observation, the example is `MISSING_STOP_OBSERVATION` rather than a legitimate right-censored row.
When vehicle identity, route, direction, ordering, timestamp, or gap rules break before resolution, the example is `SESSION_DISCONTINUITY`.
When exact schedule mapping fails, the example is `SCHEDULE_UNMATCHED`.
These states and `OVER_WIDTH_INTERVAL` remain in data-quality denominators and do not enter AFT fitting.

An example with no positive follow-up remains in the population report but is excluded from fitting with an explicit reason.
No missing destination observation is converted into a finite arrival or failure.

### 6.5 Weighting

Before deterministic population sampling, every prediction anchor receives total base weight one.
If an anchor generates `k` eligible destinations, each example receives base weight `1 / k`.
Multiple destination horizons therefore do not let one anchor dominate the source population.

The model population selects at most 300 anchors in each `(service_date, route_id, direction_id)` stratum by sorting an HMAC-SHA-256 of the anchor identifier under the versioned sampling seed.
Selection uses no feature or outcome value.
If a stratum has `N` anchors, its inclusion probability is `pi = min(1, 300 / N)` and every selected example receives analysis weight `(1 / k) / pi`.
The base weights sum to one per anchor, and the analysis weights sum to `1 / pi` per selected anchor.
Training, model selection, calibration, and final predictive metrics all use the same analysis weights.
The unsampled complete population remains authoritative for data-quality, support, and line-retention gates.

All examples from one episode and service date belong to exactly one chronological split.

## 7. Point-in-observation features

The feature cutoff is the anchor observation timestamp.

V1 feature families are:

- Route, direction, origin stop, destination stop, and route-pattern identifiers.
- Origin and destination stop sequences.
- Remaining scheduled stop count.
- Scheduled remaining travel time.
- Observed origin lateness relative to the matched scheduled origin arrival.
- Scheduled progress fraction.
- Local hour represented cyclically.
- Day of week represented cyclically.
- Weekend indicator.
- Trip start hour.
- Elapsed observed time since episode start.
- Number of stops observed before the anchor.
- Duration since the previous observed stopped sequence.
- Median duration of up to three completed same-episode segments before the anchor.
- Most recent observation gap.
- Anchor latitude, longitude, bearing, and speed when present.
- Missingness indicators for every optional numeric observation.

V1 excludes:

- Any later observation from the same episode.
- Destination arrival bounds or censoring reason.
- Final episode length or final observed stop.
- Full-day aggregates computed after the anchor.
- Future schedule versions.
- Cross-train live-state features that require missing fetch-batch provenance.
- Vehicle label or stable vehicle identity as a learned feature.
- Raw source object date as a proxy for service conditions.

The registry records each feature's type, unit, source, cutoff rule, default, and seeded leakage fixture.

Categorical model features use deterministic one-hot encoding because the retained AFT wrapper consumes a numeric matrix.
Each categorical vocabulary is fitted on training rows only, encodes missing as reserved `__MISSING__`, encodes every category absent from training as reserved `__UNKNOWN__`, and rejects raw values equal to a reserved token.
Observed training values are converted to UTF-8 strings, sorted bytewise, and placed after `__MISSING__` and `__UNKNOWN__`.
The numeric feature columns follow registry order, followed by categorical one-hot columns in registry order and then vocabulary order.
The transform emits a SciPy CSR float32 matrix so one-hot columns do not allocate a dense full-population array.
The transformation manifest stores the training-row hash, vocabularies, column ordering, CSR index dtype, float32 value dtype, missing and unknown policy, implementation version, and output-schema hash.
Validation, calibration, final-test, API, and replay scoring load that frozen transform and never extend a vocabulary.

## 8. Chronological split protocol

The split unit is MBTA service date.
No episode, observation, feature row, destination example, or derived replay crosses a split.

The frozen 2024 splits are:

| Split | Service dates | Purpose |
| --- | --- | --- |
| Training | 2024-01-01 through 2024-07-31 | Fit transforms, baselines, and candidate models. |
| Model validation | 2024-08-01 through 2024-09-30 | Select features, model family, distribution, and hyperparameters. |
| Calibration fit | 2024-10-01 through 2024-10-31 | Fit the already selected probability calibrator. |
| Final test | 2024-11-01 through 2024-12-31 | One frozen evaluation after every model and report rule is locked. |

Source integrity, schema, timestamp, schedule-match, and label-support counts may be audited across the complete year before training.
For final-test dates, Milestone 2 exposes to the retention gate only an audit projection containing outcome state, interval width, schedule-match state, predeclared slice keys, service date, and support counts.
It seals the lower and upper duration values in a content-addressed outcome partition that training, validation, calibration, and line-retention code cannot read.
The audit projection may determine whether a line has adequate measurable support, but it cannot expose duration values, threshold outcomes, model predictions, errors, or metric contributions.
Final-test duration bounds and predictive outcomes may not be used for feature selection, hyperparameter selection, model selection, calibration selection, ablation selection, or performance-based slice removal.
Milestone 4 is the first workflow authorized to open the sealed final-test duration bounds.

The acceptance configuration freezes the peak slice in `America/New_York` from the anchor observation.
`PEAK` means an ISO Monday through Friday anchor with local clock time in `[07:00:00, 10:00:00)` or `[16:00:00, 19:00:00)`.
Every other anchor is `OFF_PEAK`.
The left endpoint is included and the right endpoint is excluded at one-microsecond precision.
Calendar holidays do not override this clock classification, and a Monday-through-Friday holiday is classified by the same clock boundaries.
Milestones 0, 2, and 4 reuse the same hashed definition.

## 9. Baselines and model plan

### 9.1 Required baselines

1. Official scheduled remaining travel time as a point diagnostic.
2. A training-only empirical midpoint point diagnostic by line, direction, origin, destination, day type, and time bucket with deterministic minimum-cell backoff through line-direction-destination-offset and global destination-offset levels.
3. An intercept-only AFT distribution fitted with the same interval, left-censored, and right-censored likelihood as the candidate.
4. A feature-limited AFT distribution using only schedule and calendar features.

The schedule and empirical-midpoint diagnostics are scored only on metrics defined for point predictions or distance to a finite observed interval.
The empirical midpoint uses `(lower + upper) / 2` from training finite intervals, excludes censored rows from fitting, records its coverage loss, and is never compared by interval likelihood or eligible for promotion.
Its day type is ISO weekday versus weekend, its time bucket is the anchor's Boston local hour in `[00:00,03:00)`, `[03:00,06:00)`, and successive three-hour intervals, and a cell is usable only with at least 100 finite examples and 25 distinct anchors.
The exact backoff order is full cell, line-direction-origin-destination, line-direction-destination-offset, and global destination-offset.
Each level returns the analysis-weighted median with a lower-value tie break, and a missing global-offset cell returns unavailable rather than borrowing validation or test rows.
The intercept-only and feature-limited AFT baselines use the same examples, analysis weights, splits, censoring rules, evaluation horizons, and predictive-distribution interface as the candidate model.

### 9.2 Candidate model

The primary learned candidate is pinned XGBoost `survival:aft` with CPU `hist` training.
XGBoost accepts lower and upper ranged labels, including interval-censored and right-censored observations.

The fitted target is the positive observed duration from the anchor to the downstream stop-observation event.
Schedule-relative delay propagation is derived by subtracting the official scheduled remaining duration from each predicted or observed duration.
The residual itself is not passed to the AFT objective because it may be negative.

The candidate search compares normal, logistic, and extreme-value AFT distributions with a small frozen parameter grid.
Training uses fixed seeds, one XGBoost thread for the deterministic qualification, explicit feature ordering, and immutable row manifests.

Two diagnostic ablations are predeclared before model fitting.
`NO_PREFIX_HISTORY` removes elapsed episode time, observed-stop count, previous stopped-segment duration, three-segment median, and most-recent observation gap.
`NO_POSITION_OBSERVATION` removes anchor latitude, longitude, bearing, speed, and their associated missingness indicators.
Ablations reuse the selected candidate's distribution and hyperparameters, are trained on the training split, are inspected only on model validation before freezing, and never participate in post-test feature selection.

Inference reads the raw AFT margin and applies the selected distribution formula and scale.
The implementation must not interpret the default predicted event-time value as a probability.

### 9.3 Model selection

Model selection uses only training and model-validation dates.

Candidates are ranked by:

1. Weighted interval negative log likelihood.
2. Weighted horizon Brier score over identified threshold outcomes.
3. Worst supported-horizon calibration error.
4. Parameter count.
5. Scoring latency.
6. Bytewise model identifier.

Only predictive AFT distributions participate in this ordering.
Only bundles satisfying the frozen 10 MiB serialized-size budget are promotion-eligible, and the intercept-only AFT baseline guarantees a bounded eligible fallback.
The simplest model that wins the frozen ordering is selected before calibration-fit data is opened.
The dedicated calibration split fits the same positive-slope logistic recalibration family independently for every AFT distribution and distinct ablation bundle that will be compared on final test.
For each bundle, calibration pools the seven fixed horizons and uses only identified binary threshold outcomes.
Each destination row's analysis weight is divided equally across its identified calibration horizons so rows with more identified thresholds do not receive more total calibration weight.
The mapping is `sigmoid(a * logit(p) + b)` with `a = softplus(alpha) + 1e-6` and float64 fitting by SciPy L-BFGS-B from `alpha = 0` and `b = 0`, at most 1,000 iterations, `ftol = 1e-12`, and `gtol = 1e-8`.
Input probabilities zero and one map exactly to zero and one without evaluating infinite log odds.
The calibration family and fitting protocol are frozen before the calibration split is opened, and no calibrator affects validation-time model selection.

If the full-feature candidate does not improve on the strongest AFT baseline, the simplest passing AFT baseline is promoted.
The negative full-feature result remains a valid and publishable experiment rather than a failed software milestone.

Every promotable implementation satisfies one `PredictiveDistribution` contract for CDF evaluation, quantile inversion, metadata, dependency versions, feature schema, calibration, serialization, hashing, and loading.
An intercept-only or feature-limited bundle therefore follows the same registry and scorer path as a full-feature bundle.

### 9.4 Model outputs

One calibrated CDF produces:

- Probability of the downstream stop event within 5, 10, 15, 20, 30, 45, and 60 minutes.
- Median time to the downstream stop event.
- p80 time to the downstream stop event.
- p90 time to the downstream stop event.

Required invariants are:

- Probabilities remain within zero and one.
- A later horizon never has a lower probability.
- Quantiles remain ordered.
- Probability and quantile outputs agree with the same serialized CDF.
- A quantile beyond 60 minutes is returned as unresolved within the model horizon.
- Repeated scoring of the same bundle and feature row is stable under the documented numeric tolerance.

## 10. Evaluation protocol

### 10.1 Primary metrics

- Weighted interval negative log likelihood.
- Weighted Brier score at every fixed horizon on identified threshold outcomes.
- Complete-population lower and upper Brier bounds at every fixed horizon by assigning each unresolved threshold outcome its loss-minimizing and loss-maximizing binary value.
- Calibration error at every fixed horizon on identified outcomes plus complete-population success-rate bounds for each calibration bin.
- Median absolute distance from the prediction to the observed arrival interval.
- Interval-aware pinball-loss bounds for p50, p80, and p90.
- Empirical p50, p80, and p90 coverage bounds.
- Mean and p95 prediction-interval width.
- Resolved, right-censored, no-follow-up, and quarantined weight.

### 10.2 Slices

Every final report includes the following slice dimensions when a level is present in that split:

- Retained line and direction.
- Immediate, medium, long, and terminal destination class.
- Peak and off-peak.
- Weekday and weekend.
- Month and season.
- Short, medium, and long scheduled remaining time.
- Exact trip, platform, and stop-sequence match status.
- Low, typical, and high schedule deviation at the anchor.
- Observation-gap bucket.
- Finite interval and right-censored outcome class.

Slice definitions are fixed before dataset construction:

- Destination class is `TERMINAL` when the destination is the last scheduled sequence, otherwise `IMMEDIATE` for offset one, `MEDIUM` for offsets two through four, and `LONG` for offsets five through eight.
- Scheduled remaining time is `SHORT` for `(0, 600]` seconds, `MEDIUM` for `(600, 1,200]`, and `LONG` for `(1,200, 1,800]`.
- Anchor schedule deviation uses absolute observed lateness and is `LOW` for `[0, 60]` seconds, `TYPICAL` for `(60, 300]`, and `HIGH` above 300 seconds.
- Observation gap is `LOW` for `[0, 75]` seconds, `TYPICAL` for `(75, 180]`, and `HIGH` for `(180, 600]`; absent prior observations use an explicit `MISSING` level.
- Season is meteorological winter for December through February, spring for March through May, summer for June through August, and fall for September through November.
- Peak and off-peak use the hashed Section 8 definition.

Metric denominators are frozen by outcome eligibility:

- Interval negative log likelihood uses `INTERVAL_RESOLVED`, `LEFT_CENSORED`, and `RIGHT_CENSORED` rows and excludes `OVER_WIDTH_INTERVAL`.
- Horizon Brier and calibration point estimates use rows whose event status by that horizon is identified by the interval bounds.
- Brier and calibration bounds additionally retain every unresolved threshold row.
- Point-distance and interval-aware quantile metrics use finite-upper-bound rows and state the treatment of zero-lower left censoring.
- Outcome-class slices use their own class rows and never require a right-censored row to be resolved.

Support gates apply only to levels present in the evaluated split.
Absent calendar levels are marked not applicable, not zero-support failures.
Every table prints raw row count, distinct anchor count, distinct service-day count, analysis-weight mass, identified weight, and unresolved weight.

Calibration point estimates start with ten deterministic equal-analysis-weight bins per horizon after sorting by `(predicted_probability, example_id)`.
Scanning from lowest to highest probability, a bin with fewer than 200 distinct anchors merges into the next higher bin, or into the lower bin when it is already the highest; the scan repeats until every bin passes or only one unsupported bin remains.
Expected calibration error is the analysis-weighted mean absolute prediction-versus-outcome gap across resulting bins, and maximum calibration error is the largest supported-bin absolute gap.
Unsupported horizons and bins are reported without being pooled into another horizon.

### 10.3 Uncertainty

Final confidence intervals use exactly 2,000 deterministic bootstrap replicates over complete service-day blocks under the frozen seed.
The resampling unit is never an individual observation or destination row.
Reports use two-sided 95 percent percentile intervals from the 2.5th and 97.5th replicate percentiles with NumPy's pinned `method="linear"` quantile rule.

### 10.4 Claim discipline

Milestone acceptance proves that the experiment was complete, reproducible, and honestly reported.
It does not require a favorable learned-model result.

Public claims are generated from a machine-readable claim registry.
Every claim names its metric, split, slice, model, baseline, confidence interval, and artifact hash.

## 11. Architecture

```text
Public inventory + 368 boundary-aware MBTA objects + 2024 GTFS schedule archive
                              |
                              v
                 immutable source-object lock
                              |
                              v
       schema validation, source UTC attachment, overlap deduplication
                              |
                              v
                  normalized observations
                              |
                 +------------+------------+
                 |                         |
                 v                         v
        deterministic trip episodes   schedule lookup
                 |                         |
                 +------------+------------+
                              |
                              v
              anchors, features, and arrival intervals
                              |
                              v
         chronological manifests and weighted datasets
                              |
                              v
       baselines, AFT candidates, calibration, registry
                              |
                              v
         frozen final evaluation and evidence artifacts
                              |
                              v
                 read-only local explorer
```

The implementation remains file-based and local-first.
PyArrow processes source objects one at a time and writes partitioned Parquet.
The standard-library SQLite support may serve schedule lookups and small explorer indexes.
The model population cap keeps the maximum selected population at 658,800 anchors and 5,270,400 destination examples before later quality exclusions.

## 12. Repository transition

The repository contains useful mechanics from an earlier product shape.
The new implementation uses targeted salvage rather than preserving unrelated abstractions.

### 12.1 Retain and adapt

- `packages/ingestion` archive bounds, content hashing, immutable manifests, and schedule normalization patterns.
- `packages/features` registry hashing pattern.
- `packages/models` AFT wrapper, CDF functions, monotone calibration, and registry concepts.
- `packages/evaluation` service-day bootstrap, predictive metrics, frozen-report pattern, and gate runner.
- FastAPI loopback launch, static-file serving, and browser test harness.
- `uv`, the Python lock, Ruff, mypy, pytest, coverage, Playwright, and Make entry points.

### 12.2 Replace

- Source contracts with Bus Observatory object, vehicle observation, trip episode, and downstream example contracts.
- `TemporalView` with an observation-cutoff view that makes no product-availability assertion.
- Candidate feature rows with observed-train feature rows.
- Journey outcomes with interval and right-censored downstream stop-observation outcomes.
- Route-policy evaluation with predictive distribution evaluation.
- The service API with a read-only replay and reliability explorer.
- The active acceptance charter, milestone reports, README, architecture, data card, model card, and demonstration.

### 12.3 Retire after replacement tests pass

- The `routing` package and OpenTripPlanner tooling.
- The `decision` package.
- Boarding, transfer, recovery, and candidate-policy outcome contracts.
- Prospective collector and shadow-panel qualification surfaces.
- Mutable trip store, bearer capability, SSE, backup, and deployment-release code.
- Old gate reports, synthetic cards, screenshots, and documentation that describe the superseded product.

Git history preserves retired implementation.
The active tree must present one coherent product and one current evidence set.

### 12.4 Target layout

```text
arrive90/
  BUILD_PLAN.md
  README.md
  DATA_LICENSE.md
  pyproject.toml
  uv.lock
  Makefile
  configs/
    acceptance/travel-time-v1.1.yaml
    sources/bus-observatory-mbta-2024.yaml
    sources/mbta-gtfs-archive-2024.yaml
    features/travel-time-v1.yaml
    models/travel-time-v1.yaml
    evaluation/travel-time-v1.yaml
    source-locks/
      inventory-snapshots/
      mbta-2024.json
  packages/
    data_contracts/
    ingestion/
    features/
    outcomes/
    models/
    evaluation/
    service/
  data/
    raw/
    normalized/
    datasets/
    models/
    demo/
  artifacts/
    reports/
    cards/
    demos/
  scripts/
  tests/browser/
  docs/
```

Large `data/` and runtime artifacts remain ignored except for small deterministic fixtures and derived demo assets explicitly allow-listed.

## 13. Public CLI and real user path

Milestone 0 introduces the first coherent entry-point subset:

```text
arrive90 source lock
arrive90 source download --date 2024-05-15 --include-schedule
arrive90 data qualify-day --date 2024-05-15
arrive90 gate --milestone 0
```

The final CLI expands that same entry point to:

```text
arrive90 source lock
arrive90 source download [--year 2024 | --date YYYY-MM-DD]
arrive90 data normalize [--year 2024 | --date YYYY-MM-DD]
arrive90 data build-dataset
arrive90 model train
arrive90 model calibrate
arrive90 model evaluate
arrive90 evidence build
arrive90 serve --host 127.0.0.1 --port 8000
```

Every command reads immutable upstream manifests and writes a new versioned output directory or fails if a conflicting output already exists.
Commands print the output manifest path and SHA-256.

The primary Make workflows are:

```text
make check
make demo
make reproduce-full
make browser-test
make gate MILESTONE=N
```

`make demo` is network-free and uses the allow-listed promoted model bundle plus a separately manifested held-out replay fixture created in Milestone 4.
The fixture contains final-test feature rows and outcome intervals needed for reveal, but excludes raw vehicle identifiers, vehicle labels, trip identifiers, coordinates, and source rows.
`make reproduce-full` processes the complete 2024 lock and generates the final evidence set.

## 14. Acceptance state model

Every milestone has exactly one state:

- `NOT_STARTED`.
- `IN_PROGRESS`.
- `ACCEPTED`.
- `BLOCKED`.
- `FAILED`.

The machine-readable report key is exactly `state` and its value is one of those five uppercase strings.
Legacy `status`, `PASSED`, and `INSUFFICIENT_EVIDENCE` values are invalid under `travel-time-v1.1` rather than silently translated.

A milestone becomes `ACCEPTED` only when every acceptance item passes.
A software defect leaves the milestone `IN_PROGRESS` while it is fixed.
`BLOCKED` is reserved for an unavailable public source or another external prerequisite that prevents all remaining in-scope work.
`FAILED` records a completed experiment or gate that disproves the frozen milestone criteria.

Every milestone writes `artifacts/reports/gates/milestone-N.json` with:

- Acceptance version and charter hash.
- Source, dataset, model, and code hashes used by the milestone.
- Exact command and environment.
- Every named check and observed value.
- Final milestone state.
- Failure or blocker details when applicable.

`make gate MILESTONE=N` exits zero only for `ACCEPTED`.
No later milestone begins until the previous milestone is `ACCEPTED`.
Milestone 0 replaces the gate-report schema, shared validator, CLI runner, and active report writers before its first acceptance report is evaluated.
Contract tests cover every valid state, the four nonaccepted exit paths, a missing `state`, an unknown state, malformed JSON, mismatched milestone and acceptance version, and legacy reports.

## 15. Proposed data-quality gates

The numeric data-quality criteria below are proposed from the pre-model one-day cadence probe and are frozen before the complete-year audit.
They may be replaced only by a new acceptance version before model training.

### 15.1 One-day feasibility gate

- The pinned object hash, size, row count, schema fingerprint, and observation range match the source lock.
- The compressed official schedule archive matches its acquired-content lock, the expanded SQLite database matches its derived-artifact lock, and the pinned-day version lookup is deterministic.
- Red, Orange, and Blue are present.
- Route, trip, direction, vehicle, status, and timestamp are non-null for at least 99 percent of retained heavy-rail rows overall and at least 98 percent per proposed line.
- For the one-day support diagnostic, a trackable trip episode contains at least two distinct canonical event timestamps after the frozen gap and stop-sequence-regression split rules.
- Trackability depends only on canonical observation timestamps and cannot inspect status, schedule match, destination generation, or outcomes.
- At least 70 percent of trackable trip episodes per line contain eligible `STOPPED_AT` evidence at two or more distinct unambiguous stop sequences.
- Every episode remains in the population report and in every later full-population data-quality denominator.
- Per line, the one-day report records all, trackable, and excluded episode counts; one-observation and zero-duration counts; zero, exactly-one, and at-least-two distinct eligible `STOPPED_AT` sequence buckets for all and trackable episodes; unconditioned and trackable rates; gap-split counts; and episodes beginning after a gap split.
- The exact active-schedule matcher produces at least 500 finite or left-censored downstream examples per proposed line.
- At least 90 percent of finite arrival intervals per line are no wider than 180 seconds.
- The same source object produces byte-identical normalized and example manifests in two fresh processes.

The heavy-rail planning probe observed 13,622 Blue, 23,912 Orange, and 18,846 Red proxy pairs under the bounded destination rule.
It observed 97.33 percent of those proxy intervals at or below 180 seconds, with line p95 widths from 102 to 136 seconds.
Those observations establish feasibility but do not substitute for the executable gate.

### 15.2 Full-year retained-line gate

A line is retained only when all of the following pass on the complete source-quality audit:

- Core identity availability is at least 99 percent overall and at least 98 percent for the line.
- Exact active-schedule trip, platform, and stop-sequence match is at least 95 percent overall and at least 90 percent for every line and direction.
- At least 90 percent of generated anchor-destination examples are likelihood-eligible as interval-resolved, left-censored, or valid positive right-censored rows overall.
- At least 80 percent have that support in every line-by-direction-by-peak-or-off-peak slice.
- At least 90 percent of finite intervals are no wider than 180 seconds overall.
- At least 80 percent meet that width in every required line-by-direction-by-peak-or-off-peak slice.
- Every retained line contains at least 1,000 distinct trip episodes and at least 25 service dates in each validation, calibration, and final-test split.
- Every retained line-direction-peak-or-off-peak cell present in a nontraining split contains at least 500 likelihood-eligible examples and at least 250 distinct anchors in that split.
- Every fixed horizon reports its identified and unresolved weight, and unsupported metric-specific slice levels are visibly marked rather than pooled or silently omitted.
- Each nontraining split contains at least 100 right-censored examples overall for the censored outcome-class report, while no right-censored slice is required to contain resolved examples.

At least two of Red, Orange, and Blue must pass for the multi-line V1 claim.
If fewer than two lines pass, planning re-enters `DECIDE` for a line-specific product rather than silently lowering the gate.

## 16. Milestone plan

### Milestone 0 - Real-source vertical slice and acceptance freeze

Deliverables:

- Replace the active acceptance charter with `travel-time-v1.1` while retaining `travel-time-v1` as the predictive artifact family.
- Add the content-addressed public inventory snapshot, canonical 368-entry extractor, `InventoryLockEntry` lock, and source profile.
- Add acquired-content locks for the pinned Bus Observatory object and official 2024 schedule archive plus the expanded database's derived-artifact lock.
- Implement resumable pinned-object and schedule-archive download, exact hash verification, bounded gzip expansion, and read-only SQLite version lookup.
- Implement `InventoryLockEntry`, `AcquisitionContentEntry`, `DerivedArtifactEntry`, `VehicleObservation`, `TripEpisode`, and downstream example contracts.
- Replace the active gate-report contract, validator, `scripts/gate.py`, CLI path, and report-writer state field with the five-state Section 14 model.
- Implement the one-day Parquet schema validator, rail filter, overlap-ready observation identity, source UTC attachment, episode builder, destination generator, and arrival-interval builder.
- Implement exact pinned-day schedule matching and one real observation-cutoff feature-row view.
- Introduce the `arrive90` CLI subset declared in Section 13 and exercise the complete pinned-day qualification through it.
- Add synthetic edge fixtures for duplicates, conflicts, missing fields, invalid enums, raw time regressions, excessive gaps, stop-sequence progression, stop-sequence regression and recovery, same-timestamp ambiguity, source timestamp semantics, future schedule publication, future outcome access, over-width intervals, and censoring.
- Run the real pinned 2024-05-15 object through the public CLI.
- Write the deterministic one-day qualification report and manifest hashes.
- Update the active milestone tracker and remove old gate reports from the current evidence index.

Acceptance gate:

- Every Section 15.1 feasibility check passes.
- The pinned-day schedule, trip, platform, and stop-sequence joins are measured rather than mocked.
- Every pinned-day schedule row used by a feature was published no later than that feature's anchor cutoff.
- A source row cannot enter a different trip episode after a fresh-process rerun.
- Source UTC attachment precedes deduplication, and the pinned schedule-alignment discriminator matches Section 5.3.
- Deduplicated observations retain every exact source object and row-ordinal lineage entry.
- Every finite outcome upper bound is a later same-episode `STOPPED_AT` observation at the selected destination.
- Every feature cutoff is the anchor observation and deliberate later-observation access fails.
- Missing destination observations become right-censored or explicitly no-follow-up, never finite arrivals.
- Raw source data remains ignored and absent from Git status.
- Every gate state and malformed or legacy report control produces the frozen validator and exit behavior.
- `make check` passes.
- `make gate MILESTONE=0` passes.

Focused commit boundaries:

1. Acceptance charter and source locks.
2. Observation, episode, and target contracts with tests.
3. Real pinned-day qualification and gate evidence.

### Milestone 1 - Complete 2024 acquisition and deterministic normalization

Deliverables:

- Implement resumable direct-HTTPS downloads from the 366-object core lock plus the leading and trailing boundary objects.
- Verify response size, ETag where available, and SHA-256 for every object.
- Reverify the Milestone 0 schedule acquired-content lock and build the complete-year schedule lookup index.
- Normalize all 2024 rail observations one source object at a time.
- Handle schema evolution by required field name and explicit optional fields.
- Deduplicate overlap across adjacent objects while retaining complete lineage.
- Attach timezone-aware UTC to validated naive source timestamps under the frozen policy.
- Write route and service-date partitioned Parquet plus content-addressed manifests.
- Extract schedule versions and build the read-only schedule lookup index.
- Publish full-year row, duplicate, conflict, quarantine, gap, timestamp, storage, throughput, and schema reports.

Acceptance gate:

- All 368 locked source objects and the schedule archive are verified.
- No source object is missing, corrupt, silently replaced, or parsed under an unknown schema.
- Canonical observation identifiers are unique after deduplication.
- Every duplicate retains all contributing source keys and row ordinals.
- Conflicting duplicate states remain quarantined.
- Normalization is deterministic for a frozen representative multi-object fixture and across a process restart.
- Processing remains file-bounded and reports peak resident memory.
- Raw and normalized bulk data remain ignored.
- `make check` and `make gate MILESTONE=1` pass.

### Milestone 2 - Episodes, labels, features, and chronological dataset

Deliverables:

- Build full-year trip episodes under the frozen identity and gap rules.
- Match episodes to the correct official schedule version and route pattern.
- Generate anchors and downstream destinations without reading future observations.
- Build interval-resolved, left-censored, right-censored, over-width, and no-follow-up rows.
- Build the versioned point-in-observation feature registry and feature rows.
- Add direct `numpy` and `scipy` dependency ownership and remove dependencies used only by retired live collection.
- Generate exact chronological split manifests for the unsampled candidate population.
- Run the complete unsampled retained-line audit, then freeze the retained line, station, and destination scope before any model-population transform or selection.
- Filter the modeling population to the retained scope.
- Apply the deterministic 300-anchor-per-stratum cap to retained anchors, persist selection manifests and inclusion probabilities, and preserve the unsampled population audit.
- Generate anchor-equal base weights and inverse-inclusion analysis weights for the selected retained population.
- Fit the categorical transform on selected retained training rows only and freeze its complete vocabulary and column manifest before the DMatrix benchmark.
- Benchmark a representative DMatrix build and model fit, then project full selected-population memory, temporary storage, and runtime.
- If the projection exceeds the resource budget, implement and qualify a file-backed or sharded training path before model work begins.
- Publish the data card and full-year dataset-quality report.

Acceptance gate:

- At least two of Red, Orange, and Blue pass every Section 15.2 criterion.
- Every row belongs to exactly one split and no episode or service date crosses a boundary.
- Seeded future-observation, final-episode-length, future-schedule, post-outcome aggregate, and split-leakage defects fail through the public dataset builder.
- The full-year retention audit reads only the final-test audit projection, while any Milestone 2 read of sealed final-test duration bounds fails.
- Feature and outcome packages remain separated until the controlled join.
- Seeded validation and final-test categories absent from training map to `__UNKNOWN__`, missing values map to `__MISSING__`, and neither control changes the frozen column schema.
- No rejected line contributes a category, selected anchor, weight, transform column, or resource projection to the modeling population.
- Base weights sum to one per anchor and analysis weights sum to the inverse inclusion probability per selected anchor within numeric tolerance.
- Interval-resolved, left-censored, right-censored, over-width, no-follow-up, and quarantined examples remain distinguishable.
- The projected peak training memory is no greater than 70 percent of physical memory and projected temporary storage is no greater than 50 percent of free disk at qualification time, or the tested file-backed or sharded path satisfies those same budgets.
- The benchmark report records measured sample size, peak memory, temporary bytes, elapsed time, extrapolation method, projected runtime, and hardware manifest.
- Dataset manifests reproduce byte-identically in a fresh process from the same normalized inputs.
- `make check` and `make gate MILESTONE=2` pass.

### Milestone 3 - Baselines, calibrated AFT model, and immutable registry

Deliverables:

- Implement every Section 9.1 baseline.
- Extend the XGBoost AFT wrapper with fit, load, raw-margin inference, CDF evaluation, and complete manifest serialization.
- Run the frozen normal, logistic, and extreme-value candidate grid on training and validation data only.
- Freeze the selected model and feature schema before calibration-fit access.
- Predeclare, train, and freeze the `NO_PREFIX_HISTORY` and `NO_POSITION_OBSERVATION` ablation bundles using training and validation data only.
- Fit the frozen logistic calibration family independently to every AFT distribution and distinct ablation bundle that will be compared on final test, using only the dedicated calibration split.
- Implement model bundle validation over source, dataset, split, feature, model, calibrator, dependency, and code hashes.
- Generate validation comparison, calibration, latency, and model-selection artifacts.

Acceptance gate:

- Interval, left-censored with zero lower bound, exact, and right-censored likelihood fixtures pass.
- Raw-margin distribution fixtures fail if default event-time output is treated as probability.
- Probability bounds, horizon monotonicity, quantile ordering, CDF-quantile consistency, and 60-minute horizon behavior pass.
- Model and calibrator selection artifacts prove that final-test outcomes were unavailable.
- Every final-compared AFT and ablation bundle was trained, calibrated, serialized, and hashed before final-test access.
- Repeating deterministic training in the pinned one-thread environment reproduces predictions within the frozen tolerance and reproduces the manifest hash.
- The registry rejects a changed source, split, feature order, model, calibrator, or dependency lock.
- The strongest promotable AFT distribution is selected by the frozen ordering and runs through the common bundle interface even when it is intercept-only or feature-limited.
- The serialized promoted bundle is no larger than the 10 MiB committed-demo budget.
- `make check` and `make gate MILESTONE=3` pass.

### Milestone 4 - Frozen final evaluation and evidence package

Deliverables:

- Freeze evaluation code, model bundle, horizons, metrics, slices, bootstrap seed, and claim templates.
- Open the 2024-11-01 through 2024-12-31 final-test outcomes once.
- Evaluate the promoted model and every required baseline on identical weighted examples.
- Run exactly 2,000 complete-service-day bootstrap replicates.
- Generate calibration tables, reliability curves, interval metrics, slice tables, drift analysis, comparisons for the already frozen ablation bundles, and representative failure cases.
- Generate the machine-readable claim registry and immutable final-test report.
- Copy the exact promoted predictive-distribution bundle into the explicit demo allow-list after verifying its 10 MiB size cap and hash.
- Create a separately manifested held-out replay fixture of at most 200 examples selected by outcome-blind HMAC from November and December final-test rows.

Acceptance gate:

- Every predeclared metric and slice appears with its denominator and retained weight.
- Interval-resolved, left-censored, right-censored, over-width, no-follow-up, and quarantined mass is reported.
- Every promotable AFT distribution uses identical final examples and analysis weights.
- Point diagnostics use only their predeclared metric-eligible rows and expose excluded censored weight beside every comparison.
- Confidence intervals use complete service-day blocks.
- Learned-model underperformance, if observed, remains visible and promotes no broader claim.
- Every public numeric claim maps to the immutable final report and artifact hash.
- The committed demo bundle is byte-identical to the evaluated promoted bundle, and the replay fixture contains no raw vehicle identifier, vehicle label, trip identifier, coordinates, or source row.
- The replay selection manifest proves split provenance and outcome-blind selection while retaining source-example hashes for local lineage verification.
- A fresh report build reproduces all deterministic tables and hashes from the frozen prediction file.
- `make check` and `make gate MILESTONE=4` pass.

### Milestone 5 - Local replay explorer and portfolio-ready core

Deliverables:

- Replace the route-planner API with a read-only explorer API.
- Expose metadata, retained lines, stations, aggregate reliability, replay inventory, replay prediction, and evidence endpoints.
- Build line, direction, origin, destination, horizon, and held-out replay controls.
- Build schedule, empirical-midpoint, and promoted-model comparison cards with the diagnostics' narrower metric eligibility visible.
- Build accessible CDF, calibration, interval, and actual-reveal visualizations with text alternatives.
- Show cutoff-visible history separately from later outcome observations.
- Add data lineage, model metadata, limitations, attribution, and result links near predictions.
- Serve the allow-listed promoted bundle and held-out replay fixture through the real scorer without a network dependency.
- Rewrite Playwright tests around the new workflow.
- Produce a truthful screenshot and short recorded walkthrough artifact.
- Run the network-free demo workflow in a fresh clean checkout and write its terminal manifest.

Acceptance gate:

- The browser completes a held-out replay from selection through prediction and outcome reveal.
- The API prediction matches the offline scorer for the same replay feature row and model bundle.
- Outcome data is unavailable to the pre-reveal prediction request and cannot enter the feature payload.
- The browser shows all three quantile fields, including explicit unresolved states, fixed-horizon probabilities, diagnostics, cutoff, split, and evidence version.
- Missing model, unsupported line, unknown replay, and unavailable artifact states are understandable.
- The explorer remains usable by keyboard and without color-only distinctions.
- `make demo`, `make browser-test`, and `make check` pass in the working checkout.
- A fresh clean checkout runs the documented network-free `make demo` workflow and reproduces the expected terminal manifest before `make gate MILESTONE=5` passes.

Milestone 5 is the portfolio-ready core checkpoint.
It is not the end of V1.

### Milestone 6 - Performance, robustness, and clean reproduction

Deliverables:

- Benchmark acquisition verification, normalization, episode construction, dataset generation, training, batch scoring, API scoring, and explorer startup.
- Record p50, p95, throughput, peak memory, storage, CPU, dependency, and workload manifests.
- Add interrupted-download resume, partial-object, changed-ETag, schema drift, malformed Parquet, duplicate conflict, low-disk, corrupted model, and missing-artifact tests.
- Optimize only measured bottlenecks while preserving output hashes and model predictions.
- Add an incremental no-op rebuild that skips verified immutable work.
- Run the complete reproduction workflow against the already acquired immutable full-year raw lock in a fresh environment.

Acceptance gate:

- Every seeded infrastructure or artifact defect fails for its intended reason and its nearby control passes.
- No performance optimization changes normalized rows, dataset examples, model predictions, or evaluation outputs beyond declared numeric tolerance.
- Peak memory remains bounded independently of the full raw archive size.
- Interrupted downloads resume without accepting truncated or changed bytes.
- A no-op rerun verifies manifests and avoids rewriting immutable outputs.
- The Milestone 5 clean-demo manifest remains valid, and the full reproduction workflow produces its expected terminal manifest.
- `make check`, robustness qualification, reproduction qualification, and `make gate MILESTONE=6` pass.

### Milestone 7 - Final documentation and portfolio package

Deliverables:

- Finish the public README around the real local workflow and measured result.
- Finish architecture, ingestion, target semantics, methodology, data card, model card, evaluation, limitations, and reproduction documents.
- Publish measured data-volume, label-quality, model, calibration, slice, robustness, and performance tables.
- Add the architecture diagram, data-flow diagram, result charts, screenshot, and demonstration link or local artifact.
- Explain the owned technical contribution and how it differs from schedule-only and empirical baselines.
- Audit the active tree for stale route-planning language, placeholders, dead code, secrets, raw data, generated caches, untracked source, broken links, and unsupported claims.
- Remove retired packages, configs, reports, deployment files, and documentation after their replacement coverage passes.
- Run the complete verification suite from a clean worktree.

Acceptance gate:

- A new reader can run the demo from a clean checkout using only documented commands.
- Every README result matches a current immutable artifact.
- The active repository contains no old journey-planning, boarding, transfer, recovery, or prospective-calibration claim.
- Every required source file is tracked and every bulk or generated artifact is ignored or explicitly allow-listed.
- No placeholder, TODO, seeded defect, accidental secret, or unexplained worktree change remains.
- All source attribution and noncommercial-use notices are present.
- All milestone reports from 0 through 7 are `ACCEPTED`.
- The complete quality, browser, robustness, reproducibility, and repository-audit suites pass.
- Publication, deployment, release creation, and pull requests remain outside scope unless separately authorized.

## 17. Test strategy

### 17.1 Contract and property tests

- Canonical inventory ordering and duplicate dates.
- Separation of pre-download inventory facts from verified acquired-content facts.
- Derived schedule artifact input binding, expansion metadata, hash, size, and SQLite schema fingerprint.
- Object size, hash, and ETag mismatch.
- Required and optional Parquet schema evolution.
- Vehicle status enum parsing.
- Stable observation identity and conflicting duplicates.
- Multiple duplicate ordinals within one object and duplicate lineage across adjacent objects.
- Service-day, 24-plus-hour schedule, and Boston schedule-timezone behavior.
- Naive-UTC source attachment and a control proving that naive-Boston interpretation fails the pinned schedule-alignment discriminator.
- Episode identity, time regression, and gap splitting.
- Canonical post-deduplication observation order, ordinary stop progression, same-timestamp sequence ambiguity, stop-sequence regression split, and recovery isolation.
- Destination offsets and terminal deduplication.
- Finite interval ordering, zero-lower left censoring, and positive upper duration.
- Over-width interval state, quality denominator inclusion, and fitting exclusion.
- Right censoring and no positive follow-up.
- Anchor-equal base weighting, deterministic population selection, and inverse-inclusion analysis weighting.
- Split disjointness.
- Peak classification at every included and excluded boundary on weekdays and weekends, with a weekday holiday control.
- Training-only categorical vocabulary order, reserved-token rejection, missing mapping, unknown mapping, and stable numeric column order.
- Gate-report validation and CLI exit behavior for all five states plus malformed and legacy inputs.

### 17.2 Leakage tests

- Observation-cutoff view rejects every later source timestamp.
- Feature code cannot import outcome storage.
- Future destination status, final episode length, final stop, future schedule version, full-day aggregate, and final censoring reason each fail as seeded features.
- A schedule version published after the anchor cutoff fails matching even when its active service-date interval contains the anchor date.
- Preprocessing fitted on validation, calibration, or test service dates fails the dataset manifest.
- A validation or final-test category cannot extend or reorder the training-only feature transform.
- Calibration-family selection after calibration-fit access fails the protocol manifest.
- Any model-selection read of final-test outputs fails.
- Retention code can read the final-test support projection but cannot read sealed duration bounds, threshold outcomes, predictions, or errors.

### 17.3 Model tests

- Exact, interval-censored, and right-censored AFT rows.
- Normal, logistic, and extreme-value CDF goldens.
- Raw-margin semantics.
- Probability bounds and monotonicity.
- Quantile inversion and ordering.
- Serialization and loading.
- Deterministic training qualification.
- Calibrator monotonicity and endpoint behavior.
- Independent equal-protocol calibrator fitting for every final-compared AFT and frozen ablation bundle.
- Baseline backoff determinism.
- Model registry tamper rejection.

### 17.4 Integration and end-to-end tests

- Public inventory to verified sample object.
- Parquet object to normalized observation partitions.
- Observations to episodes, features, and outcomes.
- Dataset to baselines, model, calibrator, and registry.
- Frozen predictions to final evaluation report.
- Registered model and replay row to API response.
- Browser selection to prediction, lineage, and outcome reveal.
- Clean checkout to running demo.

### 17.5 Seeded negative controls

Every major gate includes at least one seeded defect and one nearby passing control.
The final audit confirms that each defect fails for the intended reason rather than for an unrelated parser or fixture error.

## 18. Reproducibility and artifact lineage

Every dataset manifest records:

- Source object lock hash.
- Schedule archive acquired-content hash, expanded-database derived-artifact hash, and schedule-version hashes.
- Normalizer and parser versions.
- Timezone and episode policy hashes.
- Retained-line scope.
- Feature registry hash.
- Categorical transform and output-column schema hashes.
- Peak-slice definition hash.
- Target and weighting policy hash.
- Split boundaries and row inventories.
- Partition hashes.
- Code commit and dependency lock hash.

Every model bundle records:

- Dataset and split manifest hashes.
- Training and validation row hashes.
- Feature ordering.
- Candidate grid and selected configuration.
- Predictive-distribution implementation and serialization version.
- XGBoost version and thread count when the implementation is AFT.
- Random seeds.
- Calibrator family and calibration row hash.
- Model and calibrator binary hashes.
- Demo allow-list path and content hash when the bundle is the committed promoted bundle.
- Code commit and dependency lock hash.

Every evaluation report records:

- Frozen model bundle hash.
- Final-test row and prediction hashes.
- Metric and slice configuration hash.
- Bootstrap seed, unit, and replicate count.
- Report generator version.
- Code commit and dependency lock hash.

Mutable `latest` aliases are forbidden in committed evidence.

## 19. Commit and remote verification discipline

Every verified milestone or meaningful submilestone receives a focused conventional commit.
Formatting, static checks, tests, and relevant real-path verification run before the commit.

Immediately after each commit:

1. Push the current branch to `origin`.
2. Read the current branch name and local `HEAD` SHA.
3. Read `refs/heads/<branch>` from `origin`.
4. Confirm that the remote SHA exactly equals local `HEAD`.
5. Resolve and retry any failed or mismatched push before continuing.

The repository never accumulates intentionally unpushed verified milestone work.
No pull request, release, package publication, deployment, or external artifact publication is authorized by the push instruction.

## 20. Replan triggers and bounded fallbacks

- If fewer than two lines pass the frozen full-year data gate, re-enter product-scope selection for a line-specific V1 before model fitting.
- If schedule matching fails for a line, exclude that line under the frozen rule rather than fabricating scheduled features.
- If the full-feature candidate does not beat the strongest promotable AFT baseline under the frozen validation ordering, promote the simplest passing AFT baseline and continue every evaluation and explorer milestone.
- If the final test contradicts a validation claim, publish the negative result and narrow the claim registry without changing the frozen model.
- If full-year processing exceeds local resources, preserve the file-streaming design, measure the bottleneck, and optimize the implementation without sampling away required dates.
- If a public source object becomes unavailable or changes bytes, stop at the exact missing object, preserve verified downloads, and report the canonical URL and expected lock entry needed to resume.

No current milestone requires private credentials, AWS account access, a paid service, a GPU, or remote hardware.

## 21. Final portfolio evidence

The completed repository includes:

- Canonical source inventory and source-lock report.
- Full-year data-quality and retained-scope report.
- Schema and timestamp audit.
- Trip-episode and label-quality report.
- Dataset and split manifests.
- Leakage tests with seeded failures.
- Baseline comparison table.
- Model-selection artifact.
- Calibration tables and diagrams.
- Quantile and horizon reliability tables.
- Service-day bootstrap confidence intervals.
- Line, horizon, time, season, and observation-quality slices.
- Failure-case gallery.
- Throughput, memory, storage, training, and scoring benchmarks.
- Immutable data card and model card.
- Read-only API contract.
- Browser demonstration and screenshot.
- Clean-checkout and full-reproduction evidence.
- Machine-readable public claim registry.

## 22. Authoritative references

- [Bus Observatory](https://api.busobservatory.org/)
- [Bus Observatory public inventory](https://busobservatory-lake.s3.amazonaws.com/index/data-inventory.json)
- [Bus Observatory collector](https://github.com/Cornell-Tech-Urban-Tech-Hub/BusObservatory-Grabber/tree/de653c0ab29243c9b1d64d3b425acbffc81d2822)
- [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/)
- [MBTA LAMP public data](https://performancedata.mbta.com/)
- [GTFS Schedule reference](https://gtfs.org/documentation/schedule/reference/)
- [GTFS Realtime VehiclePosition reference](https://gtfs.org/documentation/realtime/feed-entities/vehicle-positions/)
- [XGBoost AFT survival analysis](https://xgboost.readthedocs.io/en/latest/tutorials/aft_survival_analysis.html)

## 23. Final positioning

The strongest interview story is:

> I built a reproducible ML system over a complete year of public MBTA VehiclePosition data, converted minute-cadence train observations into interval-censored downstream travel-time targets, enforced observation-time leakage boundaries, compared schedule and empirical diagnostics with calibrated AFT distributions, evaluated on an untouched chronological test period with service-day uncertainty, and shipped the results through a local replay explorer with immutable lineage.

That story is complete only when every milestone from 0 through 7 is `ACCEPTED` and every measured claim maps to the final evidence artifacts.
