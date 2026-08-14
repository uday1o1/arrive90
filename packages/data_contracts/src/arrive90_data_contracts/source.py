"""Source-lock contracts for the travel-time-v1 data pipeline."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import urlparse

_BUS_OBSERVATORY_HOST = "busobservatory-lake.s3.amazonaws.com"


@dataclass(frozen=True, slots=True)
class InventoryLockEntry:
    """One pre-download object identity from a pinned public inventory."""

    inventory_snapshot_url: str
    inventory_snapshot_sha256: str
    inventory_generated_at: datetime
    inventory_date: date
    source_object_key: str
    source_url: str
    declared_size_mb: float

    def __post_init__(self) -> None:
        if self.inventory_generated_at.tzinfo is None:
            raise ValueError("inventory generated timestamp must be timezone-aware")
        if len(self.inventory_snapshot_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.inventory_snapshot_sha256
        ):
            raise ValueError("inventory snapshot SHA-256 must be lowercase hexadecimal")
        parsed = urlparse(self.source_url)
        if parsed.scheme != "https" or parsed.netloc != _BUS_OBSERVATORY_HOST:
            raise ValueError("source URL must use the public Bus Observatory HTTPS host")
        expected_key = parsed.path.removeprefix("/")
        if self.source_object_key != expected_key:
            raise ValueError("source object key must equal the URL path")
        if not self.source_object_key.endswith(".parquet"):
            raise ValueError("source object key must identify a Parquet object")
        if not math.isfinite(self.declared_size_mb) or self.declared_size_mb <= 0:
            raise ValueError("declared object size must be positive and finite")


@dataclass(frozen=True, slots=True)
class AcquisitionContentEntry:
    """Verified facts about bytes acquired from one public source URL."""

    source_object_key: str
    source_url: str
    response_size_bytes: int
    etag: str | None
    last_modified_at_utc: datetime | None
    downloaded_at_utc: datetime
    sha256: str
    schema_fingerprint: str
    row_count: int
    parser_version: str

    def __post_init__(self) -> None:
        if self.response_size_bytes <= 0 or self.row_count < 0:
            raise ValueError("content size must be positive and row count cannot be negative")
        if self.downloaded_at_utc.tzinfo is None:
            raise ValueError("download timestamp must be timezone-aware")
        if self.last_modified_at_utc is not None and self.last_modified_at_utc.tzinfo is None:
            raise ValueError("last-modified timestamp must be timezone-aware")
        for name, digest in (
            ("content", self.sha256),
            ("schema", self.schema_fingerprint),
        ):
            if len(digest) != 64 or any(
                character not in "0123456789abcdef" for character in digest
            ):
                raise ValueError(f"{name} SHA-256 must be lowercase hexadecimal")
        if not self.parser_version:
            raise ValueError("parser version is required")


@dataclass(frozen=True, slots=True)
class DerivedArtifactEntry:
    """Content lineage for deterministic transformations of acquired bytes."""

    artifact_id: str
    source_content_sha256: str
    transformation_name: str
    transformation_version: str
    transformation_parameters: tuple[tuple[str, str], ...]
    output_size_bytes: int
    output_sha256: str
    schema_fingerprint: str

    def __post_init__(self) -> None:
        if not self.artifact_id or not self.transformation_name or not self.transformation_version:
            raise ValueError("derived artifact identity and transformation are required")
        if self.output_size_bytes <= 0:
            raise ValueError("derived artifact size must be positive")
        if tuple(sorted(self.transformation_parameters)) != self.transformation_parameters:
            raise ValueError("transformation parameters must be sorted")
        for name, digest in (
            ("source content", self.source_content_sha256),
            ("output", self.output_sha256),
            ("schema", self.schema_fingerprint),
        ):
            if len(digest) != 64 or any(
                character not in "0123456789abcdef" for character in digest
            ):
                raise ValueError(f"{name} SHA-256 must be lowercase hexadecimal")
