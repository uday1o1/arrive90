# Frozen offline evaluation

Arrive90 evaluates a single confirmatory policy at reliability target 0.90 and a 20-minute maximum-extra-time cap against the static fastest candidate on identical frozen queries and candidates.
Every deadline variant retains its full frozen weight.
The primary result is the complete-population interval for the difference in deadline success, with unresolved outcomes assigned both for and against Arrive90.
The lower 95 percent complete-service-day block-bootstrap confidence limit for the worst-case lower bound must exceed zero.

The evaluation protocol is declared in `configs/evaluation/v1.yaml`.
It fixes the primary contrast, secondary Holm family, 2,000-replicate service-day bootstrap, output-support thresholds, quantile levels, deterministic recovery comparison, and release fallback order.
Final-test access is bound to SHA-256 hashes for the query and candidate manifests, learned model and calibration, support and eligibility manifests, discovery artifact, initial decision policy, transfer model and support, quantile support, recovery policy, secondary hypotheses, and evaluation code.
A final-test access token from another protocol is rejected.

Prediction reports retain unresolved decisions in their original fixed deadline band.
They publish the frozen-weighted predicted mean, success lower and upper bounds, worst-case calibration gap, counts, weighted mass, service-day blocks, and cluster-adjusted effective sample size.
Transfer reports use the same construction for fixed deciles and aggregate station calibration by decile weighted mass.
Quantile reports retain every selected-policy decision in coverage bounds and label pinball loss as conditional on finite arrival intervals.

Policy comparisons publish paired-resolution rates overall and by every registered slice.
They report partially identified success-difference bounds, a supplementary paired-resolved estimate, and added-time summaries.
The report includes predictive diagnostics, selected-policy metrics, ordinary and disruption slices, recovery against the recorded continuation, censoring bounds, availability, negative results, and the reliability-time Pareto frontier.
Recovery is secondary and always preserves the null deadline-probability and quantile contract.

Every final-test cell that was eligible before outcome access remains in the report.
If that cell fails, the policy fails and the cell cannot be suppressed after the result is known.
A changed eligibility manifest requires a new acceptance version and an untouched future test interval.
Machine-readable evaluation reports use create-only versioned paths so reruns cannot replace an earlier result.

The committed Milestone 6 qualification is synthetic and validates mechanics only.
It opens a synthetic fixture under a synthetic protocol, reproduces the discovery and report bytes from two fresh processes, and deliberately yields `HISTORICAL_EXPLORER` because synthetic evidence cannot pass the empirical gate.
The pinned container benchmark covers candidate sets of one, five, and ten plus deterministic replay generation over one day, one month, and one year on the declared 4-CPU ARM64 and 8,307,167,232-byte memory allocation.
The workload uses one origin-destination pair, one daily query time, one readiness horizon, and all 36 deadline slacks, so it is not a city-scale throughput claim.

Milestone 6 remains `INSUFFICIENT_EVIDENCE` because Milestone 0 cannot identify primitive Vehicle Position boarding evidence in the public historical export.
No production model, output-support manifest, final-test comparison, or MBTA reliability result is accepted.
The only current product-safe pivot is a historical explorer with no live recommendation claim.
