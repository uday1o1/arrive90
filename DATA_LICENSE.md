# Transit data terms and attribution

Arrive90 uses the Cornell Tech Bus Observatory archive of parsed MBTA GTFS Realtime Vehicle Positions and the official MBTA historical GTFS schedule archive.
The data-backed project is intended for noncommercial research and portfolio demonstration.

Bus Observatory documents its public archive under the Creative Commons Attribution-NonCommercial 4.0 International license.
Attribute the archive to the Jacobs Urban Tech Hub at Cornell Tech.
The upstream transportation data remains subject to the applicable MassDOT and MBTA terms and attribution.

Arrive90 is independent and is not affiliated with, endorsed by, or acting for Cornell Tech, MassDOT, or MBTA.
The project does not use their logos or protected marks as branding and makes no guarantee about source completeness, accuracy, or fitness for operational use.

## Sources

- Bus Observatory documentation: <https://api.busobservatory.org/>
- Public MBTA archive inventory: <https://busobservatory-lake.s3.amazonaws.com/index/data-inventory.json>
- Official 2024 GTFS archive: <https://performancedata.mbta.com/lamp/gtfs_archive/2024/GTFS_ARCHIVE.db.gz>
- CC BY-NC 4.0: <https://creativecommons.org/licenses/by-nc/4.0/>
- MassDOT developer resources: <https://www.mass.gov/info-details/massdot-developer-resources>

The exact source object identities, byte sizes, ETags, content hashes, schema fingerprints, and acquisition results are pinned under `configs/source-locks`.
The public documentation links are explanatory references and are not substitutes for those immutable locks.

## Repository artifact policy

| Artifact | Repository policy | Reason |
| --- | --- | --- |
| Original Parquet and schedule bytes | Download into ignored `data/raw` | Large third-party source data does not belong in Git. |
| Normalized observations and model populations | Rebuild under ignored `data/normalized` and `data/datasets` | These derived datasets preserve source lineage and remain bulk data. |
| Full trained registry | Rebuild under ignored `data/models` | The full registry is a generated result. |
| Allow-listed demo bundle and 200-row replay fixture | Commit under `artifacts/demo` | The final evaluation explicitly sanitizes and size-bounds these portfolio artifacts. |
| Aggregate reports and public charts | Commit after evidence audit | They contain no raw vehicle identifier, trip identifier, coordinate, or source row. |
| Source locks and acceptance configs | Commit | They are required to reproduce source identity and project gates. |

The committed replay fixture contains no raw vehicle identifier, vehicle label, trip identifier, coordinates, or original source row.
Redistribution of other data-derived artifacts should be reviewed against the current source terms before any external publication.

## Software licenses

Project source is available under the [MIT License](LICENSE).
The deterministic dependency inventory is recorded at [artifacts/reports/qualification/licenses-v1.json](artifacts/reports/qualification/licenses-v1.json).
It binds the project license, data terms, Python lock, and Node lock by SHA-256 and verifies license metadata for every locked package.
