# Ingestion and source lineage

## Public inputs

The 2024 observation source is the public Cornell Tech Bus Observatory compacted MBTA Vehicle Position archive.
The schedule source is the official MBTA LAMP historical GTFS database for 2024.
Neither download requires an AWS account, credentials, a payment card, or the AWS CLI.

The committed inventory lock covers every 2024 calendar date plus the 2023-12-31 and 2025-01-01 boundary objects.
The acquired-content lock contains 368 Parquet objects with 208,444,419 source rows and one compressed schedule object.
The raw storage footprint measured during qualification was 8,804,061,429 bytes after the schedule database was expanded.

## Acquisition contract

`arrive90 source lock` downloads or reads the public JSON inventory, sorts it canonically, and writes a content-addressed snapshot.
`arrive90 source download --year 2024` resolves only locked objects and verifies their response size, ETag, SHA-256, Parquet row count, and physical schema.
The schedule path also verifies the compressed SHA-256, bounded expansion, expanded database SHA-256, database size, and SQLite schema fingerprint.

Interrupted downloads use exact byte ranges.
A resumed response is accepted only if its status, `Content-Range`, ETag, final size, and final digest all match the lock.
Existing verified objects are reused without a network request.
Low available disk fails before download begins.

Four workers may acquire independent objects concurrently, but every final path remains content verified.
Normalization opens only one full source object at a time, which bounds memory independently of the full archive size.

## Normalization

Bus Observatory stores `vehicle.timestamp` as a timezone-naive value.
The pinned schedule-alignment discriminator proves that these MBTA files must be interpreted as naive UTC rather than Boston local time.
The adapter attaches UTC without clock arithmetic.

The normalizer filters Red, Orange, and Blue rail observations, validates required and optional Arrow fields, parses status values, and constructs a stable observation identity.
Exact duplicates collapse while retaining every source-row lineage entry.
Conflicting duplicates and conflicting overlaps are quarantined instead of being resolved by input order.

The accepted full-year run produced:

| Normalization measure | Observed value |
| --- | ---: |
| Source rows | 208,444,419 |
| Retained raw rows | 15,449,407 |
| Canonical observations | 11,007,856 |
| Exact duplicate rows | 4,283,843 |
| Quarantined rows | 75,705 |
| Date and line partitions | 1,098 |
| Normalized partition bytes | 677,225,116 |

The partition contract requires the complete Cartesian set of 366 service dates and three audited lines.
Each partition binds its source lineage, row count, schema, content digest, and time range.

## Schedule matching and episodes

Schedule matching requires exact platform, trip, route, direction, start date, start time, and stop sequence agreement against a schedule version published no later than the observation cutoff.
Parent stations support display grouping only and cannot substitute for an exact platform match.

After deduplication, canonical observations are ordered deterministically by episode identity, event time, stop sequence, status, source object, source row, and duplicate ordinal.
An episode splits on backward event time, a gap longer than 600 seconds, or a schedule-matched stop-sequence regression.
The recovery fragment after a regression cannot contribute prior-fragment history.

The one-day qualification generated 1,664 episodes and 45,931 downstream examples across the three audited lines.
The full-year population retained every fragment in quality denominators before Blue was selected as the only modeled line.

## Generated data policy

Raw objects live under `data/raw`.
Normalized partitions live under `data/normalized`.
Dataset populations live under `data/datasets`.
Full model registries live under `data/models`.
All four roots are ignored and rebuilt from locks.

Only compact source locks, aggregate qualification reports, and explicitly allow-listed demo artifacts enter Git.
See [DATA_LICENSE.md](../DATA_LICENSE.md) for the attribution and redistribution policy.
