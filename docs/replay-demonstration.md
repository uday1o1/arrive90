# Replay demonstration status

The Milestone 7 interface demonstration is a synthetic workflow fixture.
It exercises the real public API, single-use decision capability, trip bearer, state graph, authenticated SSE path, and deterministic recovery kernel in Chromium.
The screenshot at `artifacts/demos/milestone-7-synthetic-ui.png` labels the result as a synthetic fixture and links the current `INSUFFICIENT_EVIDENCE` evaluation card.

This artifact is not a retained historical replay and does not estimate MBTA reliability.
The required historical replay cannot be generated truthfully because the Milestone 0 audit found that the public rail export coalesces Vehicle Position and Trip Update stop timestamps without retaining provenance.
That prevents the frozen virtual-rider oracle from identifying required boarding events.

Once archived primitive Vehicle Position observations are available, resume the source audit and accepted downstream gates.
Then generate the historical replay from its immutable query, candidate, feature, outcome, model, and decision manifests.
The eventual demonstration must retain exact source hashes, service date, query and deadline variant identifiers, policy version, evaluation artifact hash, and rendered output hash.
It must never replace this blocked-state record or relabel the synthetic fixture as historical evidence.
