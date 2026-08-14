# Limitations

## Scope

Arrive90 models elapsed time from one historical train observation to a selected downstream Blue Line platform.
It does not include platform waiting, passenger readiness, access or egress walking, other rail lines, buses, commuter rail, ferries, fares, or personal mobility needs.

The replay explorer is a local evidence browser.
It does not ingest a live feed, refresh a real train prediction, send notifications, or provide operational advice.

## Source evidence

The Bus Observatory compacted Parquet files preserve parsed Vehicle Position fields and vehicle observation timestamps.
They do not preserve the original collector fetch timestamp, GTFS Realtime feed-header timestamp, or every source-fetch boundary.

The project therefore conditions features on the archived observation cutoff and exact schedule publication evidence.
It excludes cross-train live state and does not claim that every historical primitive was available to a rider-facing product at the observation timestamp.

Bus Observatory may contain gaps, parser behavior, or upstream errors not observable from the compacted archive.
The acquisition lock proves the bytes evaluated, not perfect source completeness.

## Target uncertainty

A `STOPPED_AT` observation proves that a sampled train was observed at a platform.
It does not reveal the exact physical arrival time, door opening time, or an individual passenger event.
Targets remain intervals or explicit censoring states.

The point diagnostic gives zero error when a point falls inside the arrival interval.
It is useful for comparing predictions on common eligible rows, but it should not be interpreted as exact-second arrival error.

## Generalization

Training uses January through July 2024, selection uses August and September, calibration uses October, and final evaluation uses November and December.
The final result does not establish performance for another year, a schedule redesign, rare severe disruptions, live operational feed behavior, or another line.

The full model improves interval likelihood only slightly over the strong schedule-and-calendar AFT baseline.
Its held-out point advantage is clearer, but that diagnostic excludes censored and unavailable rows.
Both facts are retained to avoid overstating the contribution.

The worst schedule-deviation bucket has materially higher NLL than the overall population.
December NLL is 0.032 higher than November NLL.
These results are descriptive warnings about harder operating conditions and possible drift.

## Calibration and uncertainty

Fixed-horizon calibration is assessed only on the frozen 2024 Blue Line population.
Long horizons are close to certain completion, so very small long-horizon Brier and calibration errors provide limited discrimination evidence.

Bootstrap intervals use 61 service-day blocks.
They represent sampling variation across days in the frozen final interval and do not account for source-system changes, future-year drift, or a different line.

## Sampling and weighting

The model population caps anchors by service date, route, and direction through outcome-blind hash ordering.
Inverse-probability weights recover the defined selected-population estimand under that sampling contract.
They do not correct source missingness, schedule-match failures outside Blue, or unobserved operating conditions.

## Local system boundary

The committed demo bundle and 200-row fixture are deliberately small and sanitized.
The explorer has no persistent user state and accepts only loopback hosts.
It has not been qualified for public hosting, concurrent production traffic, managed secrets, multi-region availability, or external service-level objectives.

The recorded performance values come from one Apple ARM64 machine with 10 logical CPUs and 25,769,803,776 bytes of physical memory.
They are workload evidence for this repository, not cloud capacity claims.

## Data and use restrictions

The data-backed project is noncommercial under the Bus Observatory CC BY-NC 4.0 terms.
The model must not be used for safety guarantees, accessibility guarantees, dispatch control, policing, employment, fare decisions, or claims about individual riders.

Any extension to a new route, year, source, target definition, feature family, or public deployment requires a new source audit, frozen acceptance version, untouched evaluation period, and terms review.
