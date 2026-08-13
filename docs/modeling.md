# Interval-censored modeling and model registry

Arrive90 pins XGBoost 3.3.0 and uses its native `survival:aft` objective through `DMatrix` lower and upper label bounds.
The wrapper runs CPU-only with one training thread, a fixed seed, complete subsampling, and the deterministic histogram tree method.
Training weights are assigned before outcome exclusion and pass directly into the matrix.

Inference requests XGBoost's raw AFT margin.
The default event-time prediction is the exponential of that margin and is not a deadline probability.
For a positive duration `t`, Arrive90 evaluates the probability as `F_Z((log(t) - margin) / scale)` using the exact configured normal, logistic, or extreme-value distribution.
Nonpositive duration has probability exactly zero.
Synthetic golden tests verify all three distributions and compare default predictions with exponentiated raw margins.

The model produces one latent arrival-time CDF.
Deadline probability is read from that CDF, and one-second bisection obtains neighboring timestamps that bracket every requested quantile.
A quantile whose probability is not reached within the 210-minute horizon remains unresolved.
The serialized CDF grid is unique and increasing.
A cumulative-maximum correction may repair only a reversal no larger than `1e-12`; a larger reversal fails validation.

One shared sigmoid calibrator transforms every threshold probability as `sigmoid(a * logit(p) + b)` with strictly positive `a`.
Exact zero and one endpoints are restored.
The transform is tested for bounds and order, and a fitted calibrator is rejected if it changes CDF ordering.
Calibration fitting accepts only identified binary cells with interior probabilities and positive frozen weights.

The transfer explanation model is separate from the arrival CDF.
Its mandatory candidates are a deterministic regularized logistic classifier and an XGBoost histogram-boosted classifier over the same supplied feature matrix and weights.
Selection first rejects candidates that miss support, latency, or slice gates, then orders survivors by weighted log loss, weighted Brier score, parameter count, and bytewise identifier.
The full transfer fit and calibration remain blocked until the conditional transfer population and chronological windows are frozen.

Pre-fit deadline support cells require at least 1,000 noncensored candidate outcomes, 500 base queries, and 30 service days.
Missing or unknown cells fail closed.
The eligibility-discovery implementation starts with every declared cell provisionally eligible, evaluates one immutable manifest, and removes every failing cell simultaneously.
An ineligible cell is absorbing.
Discovery stops only after an empty removal set and a repeated evaluation match, with an `N + 1` iteration guard and hash-chained iteration evidence.
The final production artifact still requires a true fresh-process verification on the frozen pre-test inputs.

The model registry stores immutable content hashes for the model and calibration artifacts plus feature schema, candidate mode, candidate manifest, decision context, alert lineage, eligibility mask, training rows, calibration rows, API compatibility, and library version.
It rejects any mismatch and does not expose a mutable `latest` alias.

The committed qualification proves model mechanics only.
Milestone 4 remains `INSUFFICIENT_EVIDENCE` until Milestone 3 is accepted, chronological AFT and transfer models are selected and fitted on frozen inputs, support gates pass, calibration windows are used exactly once, and a fresh process reproduces the final eligibility discovery artifact.
