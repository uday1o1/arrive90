# Historical source feasibility

Audit updated: 2026-08-14.

## Accepted source path

Arrive90 uses the public Cornell Tech Bus Observatory MBTA VehiclePosition archive for historical observations and the official MBTA 2024 GTFS archive for schedule matching.
Both sources are downloadable over HTTPS without an AWS account, credentials, or billing setup.

The Bus Observatory [public inventory](https://busobservatory-lake.s3.amazonaws.com/index/data-inventory.json) contains one `mbta_all` compacted Parquet object for every 2024 calendar date.
The source lock includes all 366 core objects plus the 2023-12-31 and 2025-01-01 boundary objects.
Raw source files remain ignored, while the inventory snapshot, object identities, and content hashes are committed.

The official historical schedule database is published at `https://performancedata.mbta.com/lamp/gtfs_archive/2024/GTFS_ARCHIVE.db.gz`.
The pipeline verifies the compressed bytes, expands the archive within a fixed size limit, verifies the derived SQLite bytes and schema, and opens the database read-only.

## Pinned one-day qualification inputs

| Input | Pinned value |
| --- | --- |
| Vehicle object | `feeds/mbta_all/COMPACTED_mbta_all_2024-05-15_13:42:26.parquet` |
| Vehicle bytes | `23,432,007` |
| Vehicle rows | `610,834` |
| Vehicle SHA-256 | `e91537e12d7cb68fd06d467e70e33a8cda02c682102098a1ce9baad7692eac73` |
| Vehicle schema fingerprint | `30f604f9dc2703280cb891abe832d6619ac11e77b81840d4dde3d54b93d32a4a` |
| Schedule gzip bytes | `150,725,341` |
| Schedule gzip SHA-256 | `de1ad0d6556683aadb4cba8af3cdfbceef2a4ea0bb4aa80ba4324d395ce29694` |
| Expanded SQLite bytes | `968,630,272` |
| Expanded SQLite SHA-256 | `89b20d64e7decb200418c00394a3a84ce2e9bb2c7a176f6d1eabc9624c8a6341` |

The normalizer retained 48,904 Red, Orange, and Blue source rows and produced 34,809 canonical observations.
Core identity availability is 100 percent overall and for every retained line.
Exact duplicates preserve all source row lineage, while 162 conflicting rows across 14 canonical identities are quarantined instead of being resolved arbitrarily.

## Timestamp semantics

The archived `vehicle.timestamp` values are timezone-naive UTC.
Normalization attaches UTC without clock arithmetic before canonical deduplication and episode construction.

The pinned discriminator matched 13,260 exact `STOPPED_AT` trip, route, direction, platform, and sequence observations to the official schedule.
Treating the source clock as UTC produced a median schedule deviation of -88 seconds, compared with 14,312 seconds under a Boston-local interpretation.
This measured discriminator governs the archive adapter.

## Trackable episode support

The first real episode run produced 1,664 deterministic trip episodes after the frozen 600-second gap and stop-sequence-regression rules.
The original one-day support denominator included singleton timestamp fragments that cannot demonstrate temporal progress.
Acceptance version `travel-time-v1.2` defines a trackable episode using only pre-outcome evidence: at least two distinct canonical event timestamps.
The 70 percent multi-stop threshold remains unchanged, every episode remains visible, and all later full-population quality gates retain every fragment.

| Line | All episodes | Trackable | Excluded | One observation | Zero duration | All zero/one/multi `STOPPED_AT` | Trackable zero/one/multi | All multi-stop rate | Trackable rate | Post-gap fragments |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | ---: | ---: | ---: |
| Blue | 596 | 376 | 220 | 220 | 220 | 225 / 7 / 364 | 7 / 5 / 364 | 61.07% | 96.81% | 9 |
| Orange | 448 | 301 | 147 | 143 | 147 | 1 / 150 / 297 | 1 / 3 / 297 | 66.29% | 98.67% | 144 |
| Red | 620 | 356 | 264 | 256 | 264 | 70 / 241 / 309 | 4 / 43 / 309 | 49.84% | 86.80% | 280 |

Trackability cannot inspect status, schedule matching, generated destinations, or outcomes.
Episodes with multiple timestamps but inadequate stopped-sequence progression remain failures in the denominator.
The separate absolute requirement of at least 500 finite or left-censored downstream examples per line remains in force.

## Evidence boundary and terms

The compacted Parquet files preserve vehicle observation timestamps but omit collector fetch timestamps and GTFS-Realtime feed-header timestamps.
The project therefore estimates travel time conditional on a historical vehicle observation and makes no claim about the earliest historical rider-visible availability of that observation.

Bus Observatory publishes the archive under CC BY-NC 4.0 with attribution to the Jacobs Urban Tech Hub at Cornell Tech.
The official schedule retains the applicable MassDOT and MBTA attribution.
