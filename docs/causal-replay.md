# Causal features and virtual-rider outcomes

Arrive90 keeps feature construction and outcome resolution in separate packages.
The feature package has no import path to the outcome package, and a repository test enforces that boundary.
Training and online feature construction both accept a `TemporalView` fixed at the query cutoff.
A record whose `product_available_at_utc` is after the cutoff is invisible even when its event time is earlier.

The current `historical_v1` registry is schedule-only because the historical operational-source gate has not passed.
It contains canonical duration, leg duration, transfer buffer, route, direction, station, transfer, time-of-day, and day-of-week features.
It explicitly excludes Trip Update predictions, future realized headways, future alerts, destination outcomes, post-journey matching, final stop counts, and next-stop-derived departures.
Deadline slack is support and evaluation metadata and cannot enter a feature row.

The virtual-rider oracle is a deterministic benchmark assumption, not an observation of a passenger.
It boards the first eligible realized train with direct Vehicle Position stop-presence evidence at or after readiness.
A downstream movement timestamp cannot satisfy boarding evidence.
Canceled, skipped, non-revenue, added, and unsupported short-turned trips remain ineligible under the exceptional-trip table.
An identity-ambiguous train that could be eligible censors the journey instead of being forced to a planned trip.

An arrived destination retains lower and upper UTC bounds.
A deadline is an identified success when the upper bound is at or before it and an identified failure when the lower bound is after it.
An interval crossing the deadline is `INTERVAL_UNRESOLVED`.
An incomplete or ambiguous observation window is `CENSORED`.
Only complete per-train reconciliation can produce `PROVEN_NO_ARRIVAL_WITHIN_HORIZON`.

Arrived candidates contribute their positive finite interval to AFT likelihood data.
A proven non-arrival contributes one right-censored interval from the observation horizon through positive infinity.
A censored candidate contributes a right-censored prefix only when observation completeness independently proves no arrival through that prefix.
Otherwise it stays in support and censoring reports with its original assigned weight but does not enter likelihood fitting.
Invalid or nonpositive bounds fail rather than being clipped.

Static schedule, official Trip Update, rolling median, empirical time-distribution, monotone threshold-logistic, point-residual, and fastest-candidate baseline components share an explicit frozen evidence context.
A context mismatch fails before metrics are compared.
The monotone logistic coefficient is projected to a nonnegative value so increasing deadline slack cannot lower its probability.
Empirical and residual distributions are constructed only from their supplied training rows.

Binary success and paired-policy comparison reports include every frozen weight.
Unresolved outcomes enter automatic best-case and worst-case bounds rather than disappearing from the denominator.
Resolved-only point estimates are therefore supplementary and cannot replace the complete-population interval.

The synthetic suite proves these contracts, but it is not empirical MBTA validation.
Milestone 3 remains `INSUFFICIENT_EVIDENCE` until the upstream milestones are accepted, one primary source semantic is frozen, the full historical population is resolved, censoring and interval-width gates pass, and every required baseline is fitted and evaluated on the frozen chronological windows.
