# Milestone 5 local API threat model

This design record is superseded for release analysis by [security.md](security.md).
It remains the local API threat model that was frozen for Milestone 5.

## Scope

This threat model covers the loopback-first Arrive90 API, its browser client, SQLite state, decision capabilities, trip bearers, and authenticated server-sent event stream.

It does not authorize a non-loopback deployment or claim that the later production-hardening milestone has passed.

## Assets

- Decision capabilities authorize exactly one trip creation for the selected recommendation.
- Trip bearer secrets authorize all reads, mutations, stops, and event streams for one trip.
- Immutable initial decision snapshots preserve cutoff, model, feature, candidate-manifest, decision-context, support, and feed lineage.
- Explicit rider-confirmed trip state controls conditional transfer and recovery guidance.
- Schedule, feed, alert, model, and support manifests determine user-visible advice.
- Service availability is bounded by request, rate, event, queue, and retention limits.

The service intentionally stores no rider identity or coordinates.

Station, ready-time, deadline, itinerary, alert, and state values are treated as sensitive observability data even though they are required inside a trip snapshot.

## Trust boundaries

- Browser input crosses an untrusted HTTP boundary before validation.
- Host, scheme, peer address, Origin, authorization, and body size are validated before route or model work.
- Forwarded headers are authoritative only when the direct peer is in the configured proxy allow-list.
- The API owns the initial cutoff, effective ready time, effective deadline, model metadata, decision context, and all session state.
- SQLite is a trusted process-local persistence boundary whose transactions must atomically consume capabilities and commit state plus event outbox rows.
- Candidate, feature, model, and support providers are trusted only through typed, immutable return contracts bound to the captured cutoff.
- Browser memory may temporarily hold plaintext capabilities and bearers, while URLs, cookies, browser persistence, and telemetry may not.

## Attackers and abuse cases

An unauthenticated network client may forge station or time inputs, exceed body limits, flood expensive search paths, replay a decision capability, guess a public trip identifier, or attempt bearer substitution.

A hostile site may attempt cross-origin trip creation or state mutation using a capability copied from browser memory.

A client may race duplicate trip creation, replay an idempotency key with a different body, submit a stale state version, forge an itinerary identifier, or open multiple event streams.

A reverse-proxy client may spoof forwarded scheme or client headers to bypass HTTPS or rate limits.

Untrusted feed or alert strings may attempt script injection or observability leakage.

An operator or exception path may accidentally log secrets, detailed requests, trip paths, or event payloads.

Unbounded request bodies, queues, streams, or retained events may exhaust memory or storage.

## Required controls

- Decision and trip secrets contain 256 bits of cryptographic randomness and are stored only as versioned HMAC-SHA256 digests.
- Digest comparisons use `hmac.compare_digest`, and unknown records follow the same comparison and response shape as known invalid records.
- Decision capabilities expire after ten minutes, are exact-recommendation bound, and are consumed atomically at most once.
- Trip sessions expire and are deleted with their event history no later than six hours after creation.
- Every trip endpoint requires the trip bearer, while every mutation also requires the exact configured Origin.
- Body size is capped at 32 KiB before semantic validation, and Pydantic rejects extra fields and values outside the frozen ranges.
- Non-loopback service mode requires HTTPS, an exact Host allow-list, and explicit trusted-proxy addresses.
- Responses use no-store caching, exact-origin CORS behavior, a restrictive content security policy, frame protection, HSTS, no-referrer, MIME-sniffing protection, and a minimal permissions policy.
- Search, trip creation, state mutation, active stream, retained event count, retained event bytes, and event age are bounded.
- State transitions use optimistic versions and idempotency keys, and the state row plus SSE outbox event commit in one SQLite transaction.
- API errors and structured audit records use route templates and reason codes without request bodies, station identifiers, itinerary identifiers, capabilities, bearers, alert text, or event payloads.
- Server-controlled strings are serialized as JSON and never rendered as trusted HTML.

## Authorization matrix

| Operation | Required authority |
| --- | --- |
| Station, system, model, and methodology reads | Exact Host and valid transport |
| Journey search | Exact Host, valid transport, exact Origin, and search rate budget |
| Trip creation | Exact Origin and one valid unconsumed decision capability bound to the exact recommendation |
| Trip read | Matching trip bearer |
| Trip state mutation or stop | Matching trip bearer, exact Origin, idempotency key, and expected state version |
| Trip event stream | Matching trip bearer and the trip's single active stream slot |

## Residual risk and release boundary

The local process stores key material in memory and receives it from explicit configuration in tests or a protected environment variable in the CLI.

Operating-system compromise, browser extension compromise, malicious dependency compromise, and denial of service above the documented local scale remain outside this milestone's protection boundary.

The default executable binds only to loopback and prints a local-development warning.

A non-loopback release remains prohibited.
Milestone 9 now implements deployment topology review, bounded key rotation, backup and restore, fault injection, dependency and container scans, and measured local load evidence, but the release gate cannot pass while the prior empirical milestones remain unaccepted.

No critical or high-severity finding remains open for the implemented loopback-only API boundary when all Milestone 5 security fixtures pass.
