# Temporal semantics

Arrive90 distinguishes the time an event describes from the time a product could have used it.

`event_time_utc` is the operational time associated with a source observation.
`source_observed_at_utc` is the time the source measured or emitted that observation when independently known.
`pipeline_known_at_utc` is the time the Arrive90 pipeline incorporated the immutable source object.
`product_available_at_utc` is the earliest evidenced time an online-equivalent feature implementation could use the primitive.
`downloaded_at_utc` records later archive acquisition and never substitutes for historical product availability.

The normal ordering is `event_time_utc <= source_observed_at_utc <= pipeline_known_at_utc <= product_available_at_utc` when every timestamp exists.
A correction retains its operational event time and receives a new knowledge and availability time.
An event timestamp is not evidence that the event was already available to the product.

Vehicle Position timestamps are observation times rather than exact passenger arrival times.
A `STOPPED_AT` observation upper-bounds the latent platform arrival unless an audited source proves stronger semantics.
A later downstream move observation can upper-bound completion at the prior station, but it never proves that the train remained observable there after rider readiness.

For an arrival interval `(L, U]`, a deadline is an identified success only when `U <= deadline`.
It is an identified failure only when `L > deadline`.
A deadline inside the interval is unresolved.
