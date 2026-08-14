# Model card

## Model status

Arrive90 has no accepted production model bundle.
The repository contains tested AFT, calibration, transfer-classification, support-discovery, registry, decision, and evaluation mechanics, but Milestone 4 remains `INSUFFICIENT_EVIDENCE` and the default service uses schedule-only abstention.

No committed result estimates real MBTA arrival probability or demonstrates an improvement over the static fastest itinerary.

## Intended model

The intended `historical_v1` model estimates one latent journey-arrival distribution for a complete candidate itinerary.
XGBoost 3.3.0 uses `survival:aft` with lower and upper outcome bounds.
A single strictly increasing sigmoid calibrator transforms threshold probabilities from that CDF.
Deadline probabilities and arrival quantiles therefore remain coherent outputs of one distribution.

A separate transfer explanation model may estimate transfer success for eligible selected-policy contexts.
Recovery selection remains deterministic and schedule-only rather than a new probability model.

## Inputs

Eligible features must be available at or before the server-owned query cutoff through the versioned feature registry.
The historical feature set excludes Trip Update prediction values.
Candidate, alert, decision-context, support, feature-schema, training-row, calibration-row, and library versions bind into an immutable model bundle.

The final eligible feature set is not frozen because the required primitive source and chronological population are unavailable.

## Training and selection protocol

Training, calibration, support discovery, and final testing use chronological immutable manifests.
The final test interval cannot influence model, feature, threshold, calibrator, or eligible-cell selection.
Support discovery removes every failing provisional cell simultaneously and makes ineligibility absorbing.
A fresh process must reproduce the final fixed-point artifact before a bundle can be registered.

Required baselines include schedule and empirical alternatives defined in the build plan.
A learned model is eligible only if it improves the frozen primary policy result under the complete-population bounds and calibration rules.
Otherwise V1 remains a schedule-only historical explorer.

## Outputs

For an eligible selected recommendation, the intended bundle can expose a deadline probability, neighboring quantile timestamps, prediction-band support, slice support, and explanation codes.
Comparator and alternative cards never expose unvalidated model outputs.
Unsupported cells, stale feeds, unavailable models, and invalid temporal horizons return explicit abstention or suppression states.

The current executable exposes null probability and quantiles, `MODEL_ABSTAINED`, and no trip-start capability.

## Evaluation

The frozen evaluation uses complete service-day bootstrap blocks, partially identified success bounds, calibration bounds that retain unresolved outcomes, deterministic recovery comparisons, and a reliability-time Pareto frontier.
Every pre-test eligible band, slice, transfer decile, transfer station, trigger decile, and quantile remains in the final report even if it fails.

Synthetic qualification validates the calculations and fresh-process byte reproduction.
It cannot satisfy an empirical gate.
See [offline-evaluation.md](offline-evaluation.md) and [evaluation-report.md](evaluation-report.md).

## Limitations and prohibited uses

The model must not be used for safety guarantees, accessibility guarantees, dispatch control, fare decisions, employment decisions, policing, or claims about individual riders.
It must not serve bus, commuter-rail, ferry, paratransit, two-transfer, or door-to-door recommendations under the V1 evidence.
It must not treat Trip Update predictions as primary outcomes or use future events as historical features.

The current mechanics have been tested only on synthetic or bounded local workloads.
They have not been trained or validated on an accepted historical MBTA outcome population.

## Promotion requirements

Promotion requires accepted Milestones 0 through 6, immutable model and support hashes, passing overall and slice calibration, passing complete-population policy bounds, fresh-process reproduction, and the unchanged `v1` decision contract.
Any post-test eligibility change requires a new acceptance version and an untouched future interval.
Prospective `v2` training must use data independent of the frozen prospective shadow panel.
