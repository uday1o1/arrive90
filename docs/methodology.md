# Methodology

## Research question

The study asks whether cutoff-visible vehicle observations improve a downstream Blue Line train travel-time distribution beyond schedule-only and empirical baselines.
The unit is one train observation anchor paired with a downstream scheduled platform one through eight stops away and no more than 1,800 scheduled seconds away.

The result does not include platform waiting or access time.

## Target semantics

Vehicle Position `STOPPED_AT` samples observe a train at a platform but do not reveal the exact physical arrival instant.
For each anchor and destination, the outcome builder finds the last earlier observation before the first later matching stopped observation.
Those observations define a latent arrival interval `(L, U]` in elapsed seconds from the anchor.

The builder preserves interval-resolved, left-censored, right-censored, over-width, missing-stop, session-discontinuity, schedule-unmatched, and no-follow-up states.
It never converts a sampled stop timestamp into an exact passenger event.

## Population and splits

The complete audit contains 479,809 episodes, 11,803,789 unsampled exact-schedule candidates, and 13,538,596 outcome records.
Blue passed the predeclared support thresholds and was frozen as the only modeled line before transforms or model fitting.

At most 300 anchors per service date, route, and direction are retained by ascending HMAC-SHA-256 under a public frozen seed.
The selection is outcome blind.
Each anchor has total base weight one, and each selected anchor receives analysis weight equal to the inverse of its inclusion probability.

The chronological split is:

| Partition | Service dates | Purpose |
| --- | --- | --- |
| Training | 2024-01-01 through 2024-07-31 | Feature vocabulary and model fit |
| Validation | 2024-08-01 through 2024-09-30 | Candidate and ablation selection |
| Calibration | 2024-10-01 through 2024-10-31 | Independent sigmoid calibration per bundle |
| Final test | 2024-11-01 through 2024-12-31 | One frozen metric-producing evaluation |

No service date or episode crosses a split.
Final-test duration bounds remain unavailable until the evaluation protocol, bundles, metrics, slices, and claims are frozen.

## Features and leakage controls

Features use only schedule facts published by the anchor cutoff and vehicle observations at or before the anchor.
The full model includes schedule, calendar, exact platform context, current position fields, and earlier observations from the same isolated episode fragment.
It excludes future observations, final episode length, outcome bounds, post-outcome aggregates, cross-train state, and Trip Update predictions.

The categorical vocabulary is fitted on selected training rows only.
Validation, calibration, and final categories absent from training map to `__UNKNOWN__`, while missing categories map to `__MISSING__`.
The frozen transform contains 88 float32 CSR columns.

Public defect probes seed future observations, future schedule versions, final episode length, post-outcome aggregates, and split leakage.
Each probe fails through the same public dataset builder that creates the real population, while its control passes.

## Models and baselines

XGBoost 3.3.0 fits `survival:aft` with lower and upper bounds, one thread, deterministic histogram trees, a fixed seed, and complete subsampling.
Candidate AFT distributions use normal, logistic, and extreme-value errors.
Inference requests the raw margin and evaluates the configured distribution CDF instead of treating XGBoost's default event-time output as a probability.

Each final-compared AFT bundle receives its own strictly increasing sigmoid calibrator fitted only on October.
The registry contains the promoted full model, two alternative full distributions, an intercept-only baseline, a schedule-and-calendar AFT baseline, and two predeclared ablations.

The official schedule and empirical midpoint are separate point diagnostics on common rows with finite upper bounds.
The schedule-and-calendar AFT is the strongest distributional baseline because it shares the same training, censoring, weighting, and calibration budget as the full model.

## Selection and final evaluation

Model selection uses validation interval negative log likelihood, frozen horizon Brier scores, calibration support, bundle size, and deterministic tie breaking.
The selected full candidate is `FULL-normal-scale-0p5` with 48 boosting rounds, depth 3, learning rate 0.08, scale 0.5, and 678 learned parameters.

The final evaluation opens November and December outcomes only after every compared bundle and the complete protocol are hash bound.
It evaluates 199,364 destination examples from 36,600 anchors on 61 service days.
Uncertainty uses exactly 2,000 complete-service-day bootstrap replicates with seed 902024.

Interval likelihood is the primary distributional measure.
Fixed-horizon Brier scores and calibration retain identified and unresolved mass separately.
Quantile distance is zero when a prediction falls inside the observed interval and otherwise measures distance to the nearest bound.
Point comparisons use only the common finite-upper population and show excluded censored weight beside the estimate.

## Reproducibility

Source locks, normalized manifests, population manifests, feature columns, model bytes, calibrator bytes, dependency locks, evaluation protocol, predictions, and reports are all content addressed.
The accepted full-year qualification rebuilt every derived stage in a fresh checkout and then verified a no-op second pass without rewriting any of 4,827 immutable output files.
