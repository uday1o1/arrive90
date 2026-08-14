# Prospective shadow protocol

The first prospective panel evaluates the frozen `historical_v1` bundle with `STATIC_ROUTE_POLICY_V1` candidates.
It does not train `prospective_v2` and does not send experimental recommendations to riders.

## State and evidence boundary

The repository currently records `NOT_FROZEN_SOURCE_GATE_BLOCKED` because the primary source-provenance gate has not passed.
No calendar interval, live result, prospective calibration claim, or 0.95 serving support is claimed by the synthetic qualification artifact.
The operational sequence is `SHAKEOUT`, `FROZEN`, `COLLECTING`, `MATURED`, followed by the report status `PASSED`, `INSUFFICIENT_EVIDENCE`, or `FAILED`.
Only a `PASSED` Milestone 8 gate can authorize a prospective calibration claim for the serving policy.
The `SHADOW_095_EVIDENCE_V1` result remains nonserving evidence even if its panel checks pass.

## Operational sequence

Run the collector for 28 distinct service days and generate a shakeout report from every scheduled query and its retained collector health artifact.
Close every operational defect before freezing the final panel.
Generate the panel JSON from supported stations and the fixed public request lattice without examining future outcomes.
The manifest must bind the accepted charter, shakeout report, candidate configuration, freshness rules, `historical_v1` bundle, support policy, decision policy, outcome resolver, online-offline parity fixture, precision calculation, and fixed end date by SHA-256.
Freeze the create-only store with:

```sh
uv run python scripts/prospective_panel.py freeze \
  --panel /absolute/path/to/panel.json \
  --shakeout-report /absolute/path/to/shakeout.json \
  --store /absolute/path/to/immutable-panel
```

At every scheduled time, record exactly one attempt, including collector, router, model, and abstention failures.
Every decided result must bind the exact feed blobs, fetch attempts, candidate manifest, feature row, model bundle, decision context, and serialized decision hash.
Record an outcome only after the scenario's frozen 210-minute resolution horizon has ended.
The create-only store rejects rewrites, unknown query identifiers, premature outcomes, `prospective_v2`, rider exposure, and a shadow selection outside `[0.95, 1.00]`.

After the fixed panel end and final outcome horizon, write the immutable report with:

```sh
uv run python scripts/prospective_panel.py report \
  --store /absolute/path/to/immutable-panel \
  --lineage-inventory /absolute/path/to/lineage-inventory.json \
  --historical-report artifacts/reports/qualification/milestone-6-report.json \
  --as-of-utc 2027-01-01T00:00:00Z \
  --bootstrap-seed 8 \
  --output /absolute/path/to/prospective-v1-report.json
```

The report keeps historical replay and prospective results in separate namespaces.
It includes all scheduled queries in availability, unresolved predictions in their original calibration bands, complete-service-day uncertainty, censoring, freshness, latency, and explicit failure counts.
A fixed panel that misses support or precision becomes immutable `INSUFFICIENT_EVIDENCE` and must not be extended after outcomes are inspected.
An integrity violation after maturity becomes `FAILED`.

## Exact prerequisite to begin

Resume at Milestone 0 with archived primitive Vehicle Position observations that preserve separate `vp_stop_timestamp` provenance, stable trip and vehicle identity, platform and status observations, observation timestamps, and product-availability lineage.
After the source gate and upstream frozen bundle pass, run the 28-service-day shakeout and then use the commands above for the single fixed final panel.
