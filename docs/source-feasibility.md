# Historical source feasibility

Audit updated: 2026-08-13.

## Current finding

An official public source now resolves the original source-discovery blocker.
The MBTA Rapid Transit Events 2022 ArcGIS item preserves actual `ARR` and `DEP` events separately from prediction fallbacks `PRA` and `PRD`.
The official event-recorder implementation emits `ARR` only from a Vehicle Position `STOPPED_AT` status and uses the Vehicle Position timestamp.

This discovery reopens Milestone 0, but it does not make Milestone 0 pass.
The complete interval-width, stop-presence, reconciliation, censoring, schedule-knowledge, scope, and query-reproduction audit remains pending.
The gate report therefore remains `FAILED` until every required measurement passes.

## Official source identity

| Source property | Pinned value |
| --- | --- |
| ArcGIS item | `99094a0c59e443cdbdaefa071c6df609` |
| Item owner | `MBTAHUB_ADMIN` |
| Access and license | Public, CC0 |
| Archive name | `Events_2022.zip` |
| Compressed bytes | `285487761` |
| Archive SHA-256 | `b47440b1886bc9d08463ac2b7e3c7fa173d982c62af0616916a59bec9c9fa478` |
| Expected coverage | 2022-01-01 through 2022-12-31 |
| Expected members | 24 monthly heavy-rail and light-rail CSV files |
| Producer source commit | `6c5db2bf6dce87b76855cda0e399f597af8cc2a1` |

The exact URLs, producer file hashes, schema, limits, and terms are pinned in `configs/sources/mbta-rapid-transit-events-2022.yaml`.
The repository-owned discovery command verifies the metadata and archive instead of trusting mutable titles or filenames.

## Producer quality findings

The complete archive scan found 24,565,356 rows across all 365 service dates and all eight proposed routes.
All source identity fields are populated.
The scan also found 89,681 repeated event-unit identities and 59 same-second semantic duplicates.
The stable source-row key is therefore the archive member plus its one-based data-row number, while downstream semantic duplicates resolve deterministically to the lexicographically first source row.

The producer-generated `event_time_sec` disagrees with a timezone-correct reconstruction on 1,627 rows.
The pinned SQL computes its timezone offset from the processing clock rather than the event clock and evaluates the two clock functions separately.
The observed differences are 525 rows at -3,600 seconds, 1,094 rows at +3,600 seconds, and eight rows at +1 second.
These are classified producer defects rather than arrival uncertainty.
The epoch `event_time` remains authoritative, and `event_time_sec` is diagnostic only.

These findings replace the initial assumptions that producer event units and service-second values would be exactly unique and consistent.
They do not change the project objective or lower an outcome-coverage threshold.
The repository keeps the anomaly counts visible and requires deterministic deduplication before evaluation.

## Evidence semantics

An `ARR` row is the first recorded Vehicle Position `STOPPED_AT` observation for its trip, vehicle, platform, and sequence.
It is an upper bound on latent physical arrival rather than an exact arrival timestamp.
It can support the virtual-rider boarding rule only when that direct observation occurs at or after readiness.

A `DEP` row is Vehicle Position movement evidence.
It can participate in a conservative interval, but it never proves boarding.
`PRA` and `PRD` are prediction fallbacks and are forbidden as primary evidence.

## Temporal availability limitation

The producer stored a feed or file timestamp internally, but the public CSV omits it.
The archive is therefore label-only for historical V1.
Its event timestamp is never copied into `product_available_at_utc`.
Historical operational features remain schedule-only, and archive availability is conservatively no earlier than the verified item modification and Arrive90 acquisition completion evidence.

## Previous LAMP finding

The current public LAMP historical rail export remains unsuitable for primary boarding evidence.
Its daily `stop_timestamp` coalesces a Vehicle Position `STOPPED_AT` timestamp with a Trip Update arrival timestamp and exports no discriminator.
The LAMP `move_timestamp` remains movement evidence only.
Newer coalesced exports are not substituted for the provenance-preserving 2022 event archive.

## Discovery and acceptance states

`make source-discovery-live` downloads the pinned source into ignored storage, streams every archive row, and writes a compact non-gate report.
A passing discovery report changes the blocker state to `PUBLIC_OFFICIAL_SOURCE_DISCOVERED_M0_AUDIT_PENDING`.
It never overwrites `artifacts/reports/gates/milestone-0.json` and never authorizes model or product claims.

Milestone 0 can become `ACCEPTED` only after the complete BUILD_PLAN gate is implemented and passes on the frozen 2022 policy.
