# Immutable ingestion and temporal source handling

Arrive90 stores GTFS Realtime fetch attempts separately from their response bytes.
Identical response bytes share one content-addressed `FeedBlob`, while every scheduled fetch, failure, timeout, and retry retains its own immutable `FetchAttempt`.
The attempt ledger uses separate transport, parse, semantic, and freshness states so an empty valid feed cannot be confused with an unavailable or malformed feed.

The collector rejects payloads above 64 MiB and entity counts above 500,000.
Parsing has a ten-second upper bound, and source timestamps more than 30 seconds in the future receive an explicit clock-skew state.
New immutable objects are subject to configured daily and total quotas.
Quota exhaustion records a bodyless `QUOTA_EXCEEDED` attempt instead of deleting older evidence.

Historical GTFS Schedule archives use the `HistoricalSourceObject` contract.
Their publication or listing time remains separate from the later acquisition time.
When no earlier publication evidence exists, schedule knowledge begins at acquisition rather than being inferred from the schedule's active dates.

Run the schedule ingestion workflow with an empty output directory and a repository-external source store:

```console
uv run arrive90-ingest-schedule \
  --archive /path/to/gtfs.zip \
  --output /path/to/normalized-service-date \
  --store /path/to/immutable-source-store \
  --source-object-id mbta-gtfs-2025-01-01 \
  --source-uri https://example.invalid/gtfs.zip \
  --service-date 2025-01-01 \
  --published-at 2024-12-20T00:00:00Z \
  --downloaded-at 2025-01-02T00:00:00Z
```

The command safely extracts the archive under a 512 MiB compressed limit, an 8 GiB expanded limit, and a 64-to-1 expansion-ratio limit.
It rejects absolute paths, traversal, links, device files, duplicate normalized paths, and reuse of a nonempty extraction or output directory.
The output manifest and sorted JSON Lines partition are byte-deterministic for identical inputs.
GTFS times above `24:00:00` remain service-day-local seconds and are not silently converted to the next civil date.

Primitive Vehicle Position and Trip Update observations remain distinguishable during normalization.
Only a direct Vehicle Position `STOPPED_AT` observation can set the primary boarding-evidence flag.
A `STOPPED_AT` timestamp is stored as an arrival upper bound and is never converted into a zero-width exact physical arrival.
A later Vehicle Position movement toward the next stop may provide a conservative departure upper bound, but it cannot prove rider boarding.
Predicted and verified-past Trip Update evidence retain their own evidence classes and cannot be silently promoted.

Alert revisions are append-only and point-in-time readable by `product_available_at_utc`.
Temporal views filter every primitive by product availability at the frozen query cutoff and raise a distinct error for an explicit request for a future-only record.

Historical observation completeness requires every eligible train to reach an explicit terminal reconciliation state with required stop intervals and row lineage.
Prospective completeness requires every scheduled attempt, fresh monotonic headers, relevant entity visibility, and bounded source gaps.
Aggregate route density cannot satisfy either rule.

Milestone 1 local code and synthetic verification are complete, but its acceptance gate remains `INSUFFICIENT_EVIDENCE` while the newly discovered official 2022 event archive undergoes the complete Milestone 0 audit.
The blocked report is generated with `make milestone1-evidence`, and `make gate MILESTONE=1` intentionally exits nonzero until that prerequisite is resolved.
