# Travel-time dataset card

## Intended use

This dataset supports a reproducible portfolio study of downstream MBTA Blue Line train travel-time distributions during calendar year 2024.
It is designed for interval-censored survival modeling, probability calibration, locked chronological evaluation, and the rider-facing Arrive90 demonstration.
It is not an operational MBTA prediction service, a safety system, or evidence for personalized accessibility guarantees.

## Source and license

The observation source is the public Cornell Tech Bus Observatory archive of parsed MBTA GTFS Realtime Vehicle Positions.
The schedule source is the official MBTA LAMP 2024 GTFS archive.
The Bus Observatory material is used under CC BY-NC 4.0 with attribution to the Jacobs Urban Tech Hub at Cornell Tech, and the underlying MassDOT attribution requirements also apply.
Raw archives, normalized observations, outcome partitions, transforms, and model artifacts remain outside Git.

## Frozen scope

Red, Orange, and Blue were audited across all 366 service dates.
Only Blue passed the frozen support gate and enters selection, features, weighting, transforms, resource projections, or later modeling.
The modeled unit is one exact-schedule Blue episode anchor paired with a downstream scheduled destination one through eight stops away and no more than 1,800 scheduled seconds away.

| Population measure | Observed value |
| --- | ---: |
| Trip episodes across audited routes | 479,809 |
| Unsampled exact-schedule candidates | 11,803,789 |
| Complete outcome records | 13,538,596 |
| Selected Blue anchors | 211,200 |
| Selected Blue destination examples | 1,151,892 |

## Blue retention evidence

The schedule-match denominator contains only source episodes whose GTFS Realtime schedule relationship is `SCHEDULED`.
Likelihood support and interval-width measurements use the complete unsampled Blue population, not the capped modeling sample.

| Gate measure | Observed value |
| --- | ---: |
| Exact scheduled match overall | 99.973% |
| Exact scheduled match direction 0 | 99.946% |
| Exact scheduled match direction 1 | 100.000% |
| Likelihood support overall | 76.992% |
| Likelihood support direction 0, off peak | 80.735% |
| Likelihood support direction 0, peak | 84.156% |
| Likelihood support direction 1, off peak | 72.760% |
| Likelihood support direction 1, peak | 72.160% |
| Finite interval width coverage overall | 98.817% |
| Finite interval width direction 0, off peak | 99.510% |
| Finite interval width direction 0, peak | 99.369% |
| Finite interval width direction 1, off peak | 98.546% |
| Finite interval width direction 1, peak | 97.332% |

## Splits, sampling, and weighting

Service dates are split into training through July 31, model validation through September 30, calibration during October, and final test during November and December.
No service date, episode, anchor, or destination example crosses a split.
At most 300 anchors are retained per service date, route, and direction by ascending HMAC-SHA-256 of the anchor identifier under the frozen public sampling seed.
Each anchor has total base weight one, and each selected anchor has total analysis weight equal to the inverse of its inclusion probability.

## Feature and outcome boundaries

Feature values are computed from observations at or before the anchor cutoff and from schedule versions published no later than that cutoff.
The categorical vocabulary is fitted on selected Blue training rows only, with `__MISSING__` and `__UNKNOWN__` reserved controls and a frozen SciPy CSR float32 schema.
Feature partitions contain no duration bounds, outcome state, final episode length, or post-outcome aggregate.
Final-test audit projections expose only aggregate support fields, while all final-test lower and upper duration bounds remain sealed until Milestone 4.
Interval-resolved, left-censored, right-censored, over-width, missing-stop, session-discontinuity, schedule-unmatched, and no-follow-up states remain distinguishable in the unsampled quality evidence.

## Resource qualification

The representative benchmark used 25,000 selected training examples and a real two-round XGBoost AFT fit.
Its projected full-population peak memory is 1,145,915,806 bytes against a 70 percent physical-memory budget.
Its projected temporary training storage is 0 bytes against a 50 percent free-disk budget.
The benchmark is a resource feasibility measurement, not a predictive-quality result.

## Reproducibility identifiers

The unsampled audit manifest SHA-256 is `e02e40b899bfa02f441aa5e2f7352e7871961eb079b5867755c4872bef8b91d7`.
The selected model-population manifest SHA-256 is `568971b631aa91ed12044182c2a3e9bd4a17274392529cb0dc9d4d43c7130cc4`.
Both are content addressed, and the Milestone 2 gate separately requires a byte-identical fresh-process population rebuild.

## Known limitations

The compacted public archive preserves Vehicle Position observation timestamps but not original fetch-batch timestamps or GTFS Realtime feed-header timestamps.
The project therefore does not claim historically exact online product availability for cross-train live state and excludes such features.
The model scope is Blue Line station-to-station train time, not platform waiting time, transfers, buses, commuter rail, ferries, door-to-door travel, or individual rider mobility.
Support and interval quality do not guarantee predictive accuracy, which is measured only after the model and evaluation protocol are frozen.
