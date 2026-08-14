# Operations guide

## Supported operating mode

The only authorized runtime mode is local loopback.
The CLI rejects non-loopback hosts, and the release-candidate container defaults to `127.0.0.1`.
Do not place the current service on a public or shared network.

Install and run the locked application.

```sh
uv python install
uv sync --frozen
uv run arrive90-api \
  --host 127.0.0.1 \
  --port 8000 \
  --state artifacts/runtime/arrive90.sqlite3
```

The process generates ephemeral HMAC keys unless protected keys are supplied through the supported environment configuration.
A restart with different keys invalidates outstanding capabilities and sessions.

## Health and evidence state

Check the non-sensitive system endpoint.

```sh
curl --fail-with-body \
  -H 'Host: 127.0.0.1:8000' \
  http://127.0.0.1:8000/v1/system/status
```

The expected current release mode is schedule-only with insufficient empirical evidence.
An unavailable source or model degrades to the named schedule fallback.
A router failure returns a stable unavailable response and never fabricates a route.
A database failure returns a constant-shape 503 response.

## State lifecycle

Decision capabilities expire within ten minutes and are single use.
Trip state, idempotency rows, recovery decisions, and SSE history expire and are deleted within six hours of trip creation.
The running application performs bounded cleanup at least once per configured cleanup interval, which cannot exceed 60 seconds.

Only `/state/arrive90.sqlite3` is mutable in the release-candidate container.
Application source should be mounted read-only, the container should run as `65532:65532`, and the runtime should disable network access unless a reviewed integration explicitly requires it.

## Backup

Stop writers or take the backup through the SQLite backup API as implemented by the management command.
Supply an explicit UTC creation time and an epoch cutoff that removes expired trip state before the copy.

```sh
uv run python scripts/manage_state.py backup \
  --state artifacts/runtime/arrive90.sqlite3 \
  --output artifacts/runtime/backup/arrive90.sqlite3 \
  --manifest artifacts/runtime/backup/manifest.json \
  --created-at-utc 2026-08-14T00:00:00Z \
  --expire-before-epoch 1786665600
```

Choose the timestamps at execution time rather than copying the example unchanged.
The command refuses missing sources, writes create-only outputs with mode `0600`, runs SQLite integrity checks, records schema and row counts, and binds the backup to a SHA-256 manifest.
The manifest declares that no rider identity, coordinates, or plaintext secrets are present.

## Restore

Restore only into a nonexistent destination.

```sh
uv run python scripts/manage_state.py restore \
  --backup artifacts/runtime/backup/arrive90.sqlite3 \
  --manifest artifacts/runtime/backup/manifest.json \
  --output artifacts/runtime/restored/arrive90.sqlite3
```

Restore verifies the content digest, schema version, SQLite integrity, row counts, and sensitive-data declarations before creating the output.
It rejects tampered manifests, tampered databases, schema drift, sensitive declarations, and an existing output path.

After restore, start a separate loopback process against the restored database, check `/v1/system/status`, and exercise an authorized trip read or create a new local search.
Old sessions remain usable only when the bounded key ring still contains their key version and their six-hour lifetime has not ended.

## Failure triage

| Symptom | Expected safe behavior | Operator action |
| --- | --- | --- |
| Source or model unavailable | Schedule fallback with null model output. | Check dependency health and lineage, then retry without changing evidence status. |
| Router unavailable | Stable 503 and no itinerary. | Restore the pinned router and graph, then repeat the same request. |
| SQLite unavailable or corrupt | Stable 503, no body echo, and no partial state event. | Stop the process, preserve the file, validate a backup, and restore to a new path. |
| SSE read failure | Stream slot released and a later authorized connection can recover. | Reconnect with the bearer and last retained event identifier. |
| Wall clock regresses | Requests fail closed with 503 until time catches up. | Repair host time synchronization and restart only after monotonic wall time is restored. |
| Feed stale | Visible stale or abstained state, never fresh guidance. | Repair collection and wait for a new captured snapshot. |

Do not change expected outputs, widen resource limits, suppress support checks, or mark an infrastructure failure as passing during incident response.

## Resource bounds

V1 caps request bodies at 32 KiB, decision capabilities at ten minutes, trip state at six hours, event streams at 100 events, 64 KiB, and ten minutes, search at 30 requests per minute, trip creation at ten per hour, state operations at 60 per minute, limiter keys at 10,000, cleanup at 60 seconds, and tolerated clock regression at five seconds.
Configuration validation rejects any larger value.

## Intended non-loopback topology

The reviewed design requires TLS termination at an exact trusted proxy, exact public Host and HTTPS Origin allow-lists, restrictive security headers, protected HMAC key injection, a persistent `/state` volume, bounded logs, and no direct plaintext listener exposure.
Forwarded headers from any untrusted peer remain ignored.

This topology is not an installation instruction.
It remains prohibited until `artifacts/reports/gates/milestone-9.json` is `PASSED` and the user explicitly authorizes deployment.

## Qualification

Run the targeted failure and recovery evidence.

```sh
make reliability-evidence
```

Run the repository and image scan.

```sh
make security-scan
make security-evidence
```

Run the complete clean-checkout procedure in [reproduction.md](reproduction.md) before treating a commit as reproducible.
