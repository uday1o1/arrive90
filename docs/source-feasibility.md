# Historical source feasibility

Audit updated: 2026-08-13.

## Current finding

An official public source resolves the original source-identity blocker.
The MBTA Rapid Transit Events 2022 ArcGIS item preserves actual `ARR` and `DEP` events separately from prediction fallbacks `PRA` and `PRD`.
The official event-recorder implementation emits `ARR` only from a Vehicle Position `STOPPED_AT` status and uses the Vehicle Position timestamp.

The complete Milestone 0 audit now fails the frozen source gate.
It scanned all 24,565,356 official event rows, reconstructed all 102 applicable schedule versions, and evaluated 975 deterministic frozen queries.
Only 25 queries were fully resolved, for a 2.5641 percent resolution rate and a 97.4359 percent censoring rate.
No proposed line passed the aggregate retention rule, so truthful scope reduction leaves no recommendation scope.
The narrowest line result was Blue at 23 percent resolution, including 18 percent peak and 28 percent off-peak resolution.
The required thresholds are 90 percent overall, 80 percent in every peak or off-peak slice, and no more than 10 percent censoring.

The resolved intervals were not the problem.
All 25 resolved arrivals were within the 300-second conservative interval-width limit, with a 61-second median and 143-second p95.
The blocker is incomplete per-train reconciliation for candidate policies, not timestamp precision.

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

## Public archive search

The Cornell Tech Bus Observatory is the strongest anonymous historical Vehicle Position archive found.
Its [public MBTA S3 listing](https://busobservatory-lake.s3.amazonaws.com/?list-type=2&prefix=feeds%2Fmbta_all%2FCOMPACTED_&max-keys=1000) contains 897 daily compacted Parquet objects from 2023-04-28 through 2025-10-22, with complete calendar-year 2024 coverage.
Its [public documentation](https://api.busobservatory.org/) describes minute-by-minute GTFS-Realtime collection and the CC BY-NC 4.0 attribution requirement.
The collector implementation is pinned at commit [`de653c0ab29243c9b1d64d3b425acbffc81d2822`](https://github.com/Cornell-Tech-Urban-Tech-Hub/BusObservatory-Grabber/tree/de653c0ab29243c9b1d64d3b425acbffc81d2822).

The compacted archive preserves trip, route, direction, vehicle, stop, stop sequence, Vehicle Position status, source vehicle timestamp, and coordinates.
It does not preserve Trip Updates, explicit cancellations, the collector fetch timestamp, the original fetch batch, or the GTFS-Realtime feed-header timestamp.
Its source timestamps are timezone-naive Boston local values.
S3 `Last-Modified` values cannot repair this gap because older objects were migrated or reuploaded in November 2025.

A bounded 2024-05-15 diagnostic joined the official 2024 event export, official 2024 schedule archive, and the public Bus Observatory object.
The union matched 73.45 percent to 91.67 percent of scheduled active trips by line.
The Bus Observatory object added at most one matched scheduled trip per line over the official event export on that day.
It therefore adds useful trajectory evidence but does not identify cancellations or close the per-train completeness gate.
The exact object identities, hashes, schema limitation, and line results are recorded in `artifacts/reports/qualification/milestone-0-public-source-assessment-v1.json`.

The official MBTA [`prediction_loc`](https://github.com/mbta/prediction_loc) repository includes a `scripts/getArchive.py` client for archived subway Trip Updates.
Its README requires an AWS access key and secret from an MBTA AWS administrator and a bucket name from MBTA 1Password.
That private archive is the closest identified source that could contain explicit cancellation states, but it is not an anonymous public resource.

## Exact blocker and resume choices

The official event item explicitly says the data is not guaranteed complete for any stop or date.
An absent `ARR` or `DEP` row therefore cannot prove that a scheduled train was canceled, skipped, short-turned, non-revenue, or merely missing from telemetry.
The BUILD_PLAN forbids converting that uncertainty into success or non-arrival and requires the candidate to remain censored.

Milestone 0 can resume only through one of these paths:

1. Obtain an authorized complete 2022 MBTA archived subway GTFS-Realtime export with Vehicle Positions, Trip Updates, feed headers, fetch-object timestamps, and explicit cancellation or skip relationships, then rerun `make audit-milestone0` with immutable pinned inputs.
2. Collect equivalent prospective fetch-attempt and entity evidence for the plan's 28-service-day shakeout and 56-service-day shadow periods, then create a new untouched acceptance interval before making a live recommendation claim.
3. Apply the BUILD_PLAN kill gate and select another ML product, such as a historical MBTA reliability explorer or a conditional travel-time model whose target does not require proving the first eligible train among unseen schedules.

The current recommendation model cannot proceed to Milestone 1 with a failed Milestone 0 gate.

## Previous LAMP finding

The current public LAMP historical rail export remains unsuitable for primary boarding evidence.
Its daily `stop_timestamp` coalesces a Vehicle Position `STOPPED_AT` timestamp with a Trip Update arrival timestamp and exports no discriminator.
The LAMP `move_timestamp` remains movement evidence only.
Newer coalesced exports are not substituted for the provenance-preserving 2022 event archive.

## Discovery and acceptance states

`make source-discovery-live` downloads the pinned source into ignored storage, streams every archive row, and writes a compact non-gate report.
A passing discovery report changes only the source-identity state.
It never overwrites `artifacts/reports/gates/milestone-0.json` and never authorizes model or product claims.

Milestone 0 remains `FAILED` because the complete frozen 2022 audit recommends no supported line or transfer station.
