# Transit data license and attribution

MassDOT provides the transportation data used by Arrive90.
Arrive90 is independent and is not affiliated with, endorsed by, or acting for MassDOT or MBTA.

The official MassDOT Developers License Agreement grants non-exclusive, limited, and revocable rights to use, reproduce, and redistribute the data after acceptance of its terms.
It requires clear acknowledgment of MassDOT as the data provider.
It prohibits representing the project as MassDOT or a partner, using protected logos or trademarks with the data, misrepresenting the data, making guarantees about the data, or claiming ownership of the data.

The source document reviewed for Milestone 0 was the four-page PDF currently returned by the official download URL on 2026-08-13.
Its SHA-256 digest was `6962a9dd3abac0ce700af47da0136739615af621fee21a4ef8a8cf693b540a95`.
The document labels its terms as updated on 2009-11-13 and reserves MassDOT's right to change or revoke them.

## Redistribution and retention matrix

| Artifact | Repository policy | Basis |
| --- | --- | --- |
| Original MassDOT or MBTA feed bytes | Regenerate outside Git | Redistribution is permitted by the reviewed license, but mutable and large source data does not belong in source control. |
| Historical LAMP Parquet files | Regenerate outside Git | Redistribution is permitted by the reviewed license, but files are mutable public exports and are retained by content hash outside Git. |
| Normalized transit rows | Regenerate outside Git | Derived rows remain data and are stored outside Git with source lineage. |
| Trained model binaries | Regenerate outside Git | Models can encode source-derived information and are retained in the external immutable artifact store. |
| Aggregate metrics without restricted or identifying content | May be committed | The reviewed license permits use and redistribution with attribution and truthful representation. |
| Small synthetic fixtures | May be committed | Fixtures contain no MassDOT source rows. |
| MBTA or MassDOT logos and marks | Never commit as branding | The reviewed license prohibits logo and trademark use in connection with the data. |
| Product screenshots | May be committed after review | Screenshots must contain attribution, no protected marks, no rider identity, and only accepted claims. |

The license may change without notice.
Release verification must re-download, hash, and review the current terms.
