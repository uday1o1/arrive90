"""Immutable storage for historical archives and their canonical manifests."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict
from datetime import date, datetime
from enum import Enum
from pathlib import Path

from arrive90_data_contracts.realtime import HistoricalSourceObject


def canonical_json_bytes(value: object) -> bytes:
    """Serialize a manifest reproducibly across runs and machines."""

    def default(item: object) -> str:
        if isinstance(item, (date, datetime, Enum)):
            return item.isoformat() if isinstance(item, date) else str(item.value)
        raise TypeError(f"cannot encode {type(item).__name__}")

    return (
        json.dumps(value, default=default, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()


class HistoricalObjectStore:
    """Retain source bytes once and fail on mutable source-object identifiers."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.blob_root = root / "blobs"
        self.manifest_root = root / "manifests"
        self.blob_root.mkdir(parents=True, exist_ok=True)
        self.manifest_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _atomic_write(path: Path, body: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix="object-", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            if path.exists():
                if path.read_bytes() != body:
                    raise ValueError(
                        f"immutable object already exists with different bytes: {path.name}"
                    )
            else:
                os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def record(self, source: HistoricalSourceObject, body: bytes) -> tuple[Path, Path]:
        digest = hashlib.sha256(body).hexdigest()
        if digest != source.blob_sha256:
            raise ValueError("historical source digest does not match body")
        blob_path = self.blob_root / digest[:2] / digest[2:]
        manifest_path = self.manifest_root / f"{source.source_object_id}.json"
        manifest_body = canonical_json_bytes(asdict(source))
        if manifest_path.exists() and manifest_path.read_bytes() != manifest_body:
            raise ValueError(
                f"immutable object already exists with different bytes: {manifest_path.name}"
            )
        self._atomic_write(blob_path, body)
        self._atomic_write(manifest_path, manifest_body)
        return blob_path, manifest_path
