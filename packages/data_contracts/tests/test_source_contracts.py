from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime

import pytest
from arrive90_data_contracts.source import (
    AcquisitionContentEntry,
    DerivedArtifactEntry,
    InventoryLockEntry,
)

_DIGEST = "a" * 64


def test_inventory_lock_entry_requires_canonical_public_identity() -> None:
    entry = InventoryLockEntry(
        inventory_snapshot_url="https://busobservatory-lake.s3.amazonaws.com/index/data-inventory.json",
        inventory_snapshot_sha256=_DIGEST,
        inventory_generated_at=datetime(2026, 8, 14, tzinfo=UTC),
        inventory_date=date(2024, 5, 15),
        source_object_key="feeds/mbta_all/object.parquet",
        source_url="https://busobservatory-lake.s3.amazonaws.com/feeds/mbta_all/object.parquet",
        declared_size_mb=22.4,
    )
    assert entry.inventory_date == date(2024, 5, 15)

    with pytest.raises(ValueError, match="object key"):
        replace(entry, source_object_key="feeds/mbta_all/different.parquet")
    with pytest.raises(ValueError, match="timezone-aware"):
        replace(entry, inventory_generated_at=datetime(2026, 8, 14))
    with pytest.raises(ValueError, match="snapshot SHA-256"):
        replace(entry, inventory_snapshot_sha256="bad")
    with pytest.raises(ValueError, match="public Bus Observatory"):
        replace(entry, source_url="https://example.com/feeds/mbta_all/object.parquet")
    with pytest.raises(ValueError, match="Parquet"):
        replace(
            entry,
            source_object_key="feeds/mbta_all/object.csv",
            source_url="https://busobservatory-lake.s3.amazonaws.com/feeds/mbta_all/object.csv",
        )
    with pytest.raises(ValueError, match="positive and finite"):
        replace(entry, declared_size_mb=0)


def test_content_entry_validates_hashes_timestamps_and_counts() -> None:
    entry = AcquisitionContentEntry(
        source_object_key="feeds/mbta_all/object.parquet",
        source_url="https://busobservatory-lake.s3.amazonaws.com/feeds/mbta_all/object.parquet",
        response_size_bytes=10,
        etag=None,
        last_modified_at_utc=None,
        downloaded_at_utc=datetime(2026, 8, 14, tzinfo=UTC),
        sha256=_DIGEST,
        schema_fingerprint="b" * 64,
        row_count=1,
        parser_version="test-v1",
    )
    assert entry.row_count == 1
    with pytest.raises(ValueError, match="row count"):
        replace(entry, row_count=-1)
    with pytest.raises(ValueError, match="download timestamp"):
        replace(entry, downloaded_at_utc=datetime(2026, 8, 14))
    with pytest.raises(ValueError, match="last-modified"):
        replace(entry, last_modified_at_utc=datetime(2026, 8, 14))
    with pytest.raises(ValueError, match="content SHA-256"):
        replace(entry, sha256="bad")
    with pytest.raises(ValueError, match="schema SHA-256"):
        replace(entry, schema_fingerprint="bad")
    with pytest.raises(ValueError, match="parser version"):
        replace(entry, parser_version="")


def test_derived_artifact_requires_sorted_parameters_and_bound_hashes() -> None:
    artifact = DerivedArtifactEntry(
        artifact_id="schedule.sqlite",
        source_content_sha256=_DIGEST,
        transformation_name="gzip-expand",
        transformation_version="python-3.12",
        transformation_parameters=(("mode", "bounded"),),
        output_size_bytes=100,
        output_sha256="b" * 64,
        schema_fingerprint="c" * 64,
    )
    assert artifact.output_size_bytes == 100
    with pytest.raises(ValueError, match="sorted"):
        replace(artifact, transformation_parameters=(("z", "1"), ("a", "2")))
    with pytest.raises(ValueError, match="identity"):
        replace(artifact, artifact_id="")
    with pytest.raises(ValueError, match="positive"):
        replace(artifact, output_size_bytes=0)
    with pytest.raises(ValueError, match="source content SHA-256"):
        replace(artifact, source_content_sha256="bad")
    with pytest.raises(ValueError, match="output SHA-256"):
        replace(artifact, output_sha256="bad")
    with pytest.raises(ValueError, match="schema SHA-256"):
        replace(artifact, schema_fingerprint="bad")
