# Local service API

## Evidence boundary

The Milestone 5 service mechanics are implemented and locally tested, but no learned reliability policy is accepted while Milestone 0 remains failed.

The default executable therefore returns deterministic schedule guidance with `MODEL_ABSTAINED`, null probability and quantiles, no decision capability, and trip start disabled.

Tests inject immutable synthetic score bundles to qualify the complete authorization, trip-state, event, and recovery mechanics without representing those values as empirical MBTA evidence.

## Run the loopback service

Install the locked environment and start the API.

```sh
uv sync --frozen
uv run arrive90-api --host 127.0.0.1 --port 8000
```

The executable rejects non-loopback hosts before startup.

Runtime SQLite state is written under `artifacts/runtime/` by default and is ignored by Git.

The process generates ephemeral decision and trip HMAC keys, so restarting the local demonstration invalidates existing capabilities and sessions.

## Inspect status and stations

```sh
curl --fail-with-body \
  -H 'Host: 127.0.0.1:8000' \
  http://127.0.0.1:8000/v1/system/status

curl --fail-with-body \
  -H 'Host: 127.0.0.1:8000' \
  http://127.0.0.1:8000/v1/stations
```

## Search the degraded local schedule

Supply current UTC timestamps whose ready time is no more than 15 minutes ahead and whose deadline is 5 through 180 minutes after readiness.

```sh
curl --fail-with-body \
  -H 'Host: 127.0.0.1:8000' \
  -H 'Origin: http://127.0.0.1:8000' \
  -H 'Content-Type: application/json' \
  --data '{
    "origin_station_id": "demo-origin",
    "destination_station_id": "demo-destination",
    "ready_at": "2026-08-14T00:07:00Z",
    "deadline": "2026-08-14T00:37:00Z",
    "reliability_target": "0.90",
    "maximum_extra_minutes": 20
  }' \
  http://127.0.0.1:8000/v1/journeys/search
```

Use timestamps current at execution time rather than copying the example literally.

The response exposes the server-owned data cutoff, requested and effective times, normalization status, feed and support state, candidate and decision-context hashes, comparator, recommendation, explanation codes, and limitations.

Only the recommendation slot can contain a validated probability or quantile in an accepted backend.

Comparator, backup, and alternative slots always report `NOT_SELECTED_OUTPUT_UNVALIDATED` with null model output.

## Capabilities and trip sessions

An accepted trip-startable search returns one 256-bit `decision_id` capability for the exact recommended itinerary.

The capability expires after ten minutes and can create at most one trip.

Trip creation returns a separate 256-bit bearer exactly once.

Every trip read, state change, stop, and event stream requires `Authorization: Bearer <trip_bearer>`.

State-changing calls also require the exact configured `Origin` header.

The client must retain capabilities and bearers in memory only and must never put them in a URL, cookie, persistent browser storage, analytics event, error report, or log.

The event endpoint is consumed as an authenticated fetch stream because browser `EventSource` cannot attach the required authorization header.

## State and recovery behavior

Direct journeys board through `NOT_STARTED -> ON_FINAL_LEG`.

One-transfer journeys follow `NOT_STARTED -> ON_FIRST_LEG -> AT_TRANSFER -> ON_FINAL_LEG`.

Every state request carries an idempotency UUID, expected state version, requested next state, and a server-issued itinerary or route-pattern identifier when boarding.

At `AT_TRANSFER`, an eligible recovery backend may issue a bearer-protected recovery decision bound to the trip, station, state version, selectable policies, and ten-minute expiry.

Recovery ranking is schedule-only, exposes no new deadline probability or arrival quantile, and never emits a reliability-target status.

## Security and resource bounds

The API enforces exact Host and Origin allow-lists, a 32 KiB body limit, trusted-proxy restrictions, no wildcard CORS, no-store caching, restrictive browser headers, fixed initial rate limits, one active event stream per trip, and bounded event retention.

Authorization and validation failures do not echo request bodies, station values, timestamps, itinerary values, capabilities, or bearers.

See [security.md](security.md) for the intended topology, assets, trust boundaries, controls, scan evidence, and release boundary.
See [threat-model-milestone-5.md](threat-model-milestone-5.md) for the original local API design record.
