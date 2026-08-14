# Acceptance charter status

`configs/acceptance/v1.yaml` records the non-negotiable V1 rules and the frozen source-audit policy.
The candidate historical interval is 2022-01-01 through 2022-12-31 because the official provenance-preserving event archive covers that complete year.

The conservative station-departure interval is frozen as the primary outcome-time semantic before the full outcome audit.
Direct Vehicle Position arrival intervals remain a required diagnostic.
Prediction fallback sensitivity remains separate.

The initial proposed routes are Red, Orange, Blue, Green-B, Green-C, Green-D, Green-E, and Mattapan.
The exact aggregate whole-line or whole-station exclusion rules are frozen before outcome inspection.
No individual service date, query, disruption, source outage, ambiguous trip, or incomplete outcome window may be removed.

Historical operational feature families are frozen empty because the public event archive omits query-time file availability.
Historical V1 is schedule-only.

The supported scope remains unfrozen until the predeclared aggregate rules are applied to the full audit evidence.
The charter does not lower a threshold or convert missing evidence into a pass.
Milestone 0 remains failed until every coverage, interval, presence, reconciliation, censoring, schedule, license, and reproduction gate passes.
