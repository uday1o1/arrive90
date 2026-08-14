# Acceptance charter status

The active acceptance charter is `configs/acceptance/travel-time-v1.1.yaml`.
It governs the local-first 2024 MBTA rail downstream travel-time reliability experiment described in `BUILD_PLAN.md`.
The predictive artifact family remains `travel-time-v1`.

The proposed lines are Red, Orange, and Blue, with at least two required to pass the complete-year retention gate.
The primary event is the first later canonical `STOPPED_AT` observation at a selected downstream scheduled platform and sequence within the same deterministic trip episode.
Finite, left-censored, right-censored, missing-stop, no-follow-up, schedule-unmatched, discontinuity, and over-width states remain distinct.

The one-day feasibility gate uses only pinned public source bytes and the exact official schedule version available by each observation cutoff.
Its trackable-episode support diagnostic requires at least two distinct canonical event timestamps before inspecting status, schedule, destinations, or outcomes.
Every episode remains visible in the report and in later full-population quality denominators.

Milestone reports use the exact state key `state` and one of `NOT_STARTED`, `IN_PROGRESS`, `ACCEPTED`, `BLOCKED`, or `FAILED`.
Only `ACCEPTED` unlocks the next milestone.
The active milestone tracker is `configs/acceptance/travel-time-v1.1-milestones.json`.
