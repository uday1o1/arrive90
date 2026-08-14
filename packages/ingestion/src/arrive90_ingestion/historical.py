"""Canonical manifest serialization shared by immutable data stages."""

from __future__ import annotations

import json
from datetime import date, datetime
from enum import Enum


def canonical_json_bytes(value: object) -> bytes:
    """Serialize a manifest reproducibly across runs and machines."""

    def default(item: object) -> str:
        if isinstance(item, date | datetime):
            return item.isoformat()
        if isinstance(item, Enum):
            return str(item.value)
        raise TypeError(f"cannot encode {type(item).__name__}")

    return (
        json.dumps(value, default=default, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
