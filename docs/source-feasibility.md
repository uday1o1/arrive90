# Historical source feasibility

Audit date: 2026-08-13.

## Finding

The current public LAMP historical rail exports do not satisfy the V1 primary boarding-evidence contract.
The finding is a source-provenance failure, not a model-quality result.

The current LAMP data dictionary defines the daily subway `stop_timestamp` as the earliest Vehicle Position `STOPPED_AT` timestamp or, when that is absent, a Trip Update arrival timestamp.
The current `flat_file.py` implementation exports `COALESCE(vp_stop_timestamp, tu_stop_timestamp)` under that one field name.
Neither the daily subway schema nor the `LAMP_ALL_RT_fields` schema exports `vp_stop_timestamp`, `tu_stop_timestamp`, or an evidence discriminator for rail.

The daily export does expose `move_timestamp`, which comes from Vehicle Position movement evidence.
That field can support a conservative station-departure upper bound for destination timing when paired with a defensible lower bound.
The acceptance charter explicitly prohibits treating a downstream move timestamp as proof that a train remained observed at an earlier boarding platform.
It therefore cannot repair the missing boarding evidence.

## Source versions

| Source | Audited value |
| --- | --- |
| LAMP repository commit | `6b30a81b3e9dfedbf2b6bbb4abcd2d8bdd5289c5` |
| LAMP public index SHA-256 | `c1458cd555911275f875e964ae008e0ff13f3d27b483231a2a7b9a102b6ee11a` |
| LAMP index coverage observed | 2019-09-15 through 2026-08-13 |
| Representative Parquet date | 2025-07-01 |
| Representative Parquet SHA-256 | `86f5c9bd9c40e16a393d0242b302162505bea2a7cd4aaa11557e330d0436a412` |
| MassDOT license PDF SHA-256 | `6962a9dd3abac0ce700af47da0136739615af621fee21a4ef8a8cf693b540a95` |

## Temporal availability limitation

The historical daily files are retrospective derived exports.
Their current object modification timestamps do not prove when each primitive event first became available to an online-equivalent consumer.
An event timestamp cannot be copied into `product_available_at_utc` without independent source or transformation evidence.
Operational features that depend on primitive historical availability therefore remain unsupported until the required source lineage is available.

## Exact prerequisite to resume

Milestone 0 can resume when an authorized historical source provides, for the proposed interval:

- Vehicle Position stop and movement primitives with stable trip, vehicle, platform, status, and observation timestamps.
- Vehicle Position `STOPPED_AT` values separately identifiable from Trip Update predictions.
- Source-observation and earliest product-availability evidence that is not retrospectively backdated.
- Sufficient per-train continuity to reconcile every potentially eligible train through each frozen observation horizon.

An updated public LAMP rail export that exposes `vp_stop_timestamp` and its availability lineage would satisfy the shape of the prerequisite.
Access to the archived primitive Vehicle Position objects used by LAMP would also satisfy the shape of the prerequisite if its license and retention terms permit this use.

Until then, the audit must remain `FAILED`, supported scope must remain empty, and no recommendation model, probability, or calibration claim is accepted.
