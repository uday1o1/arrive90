"""Deterministic Bus Observatory inventory snapshot and lock generation."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from arrive90_data_contracts.gates import DEFAULT_ACCEPTANCE_VERSION
from arrive90_data_contracts.source import InventoryLockEntry

from arrive90_ingestion.historical import canonical_json_bytes

INVENTORY_URL = "https://busobservatory-lake.s3.amazonaws.com/index/data-inventory.json"
FEED_ID = "mbta_all"
LOCK_VERSION = "arrive90-bus-observatory-inventory-lock-v1"
FIRST_BOUNDARY_DATE = date(2023, 12, 31)
FIRST_CORE_DATE = date(2024, 1, 1)
LAST_CORE_DATE = date(2024, 12, 31)
LAST_BOUNDARY_DATE = date(2025, 1, 1)
EXPECTED_OBJECT_COUNT = 368
MAX_INVENTORY_BYTES = 10 * 1024 * 1024


class InventoryError(ValueError):
    """Raised when a public inventory cannot produce the frozen lock."""


def _sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _validate_inventory_url(url: str) -> None:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "busobservatory-lake.s3.amazonaws.com"
        or parsed.path != "/index/data-inventory.json"
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise InventoryError("inventory URL must be the canonical public Bus Observatory index")


def download_inventory(url: str = INVENTORY_URL) -> bytes:
    """Download the bounded public inventory from its canonical HTTPS URL."""

    _validate_inventory_url(url)
    request = Request(  # noqa: S310 - URL is strictly allow-listed above.
        url, headers={"User-Agent": "arrive90/travel-time-v1"}
    )
    with urlopen(request, timeout=60) as response:  # noqa: S310 - Request URL is allow-listed.
        body = bytes(response.read(MAX_INVENTORY_BYTES + 1))
    if len(body) > MAX_INVENTORY_BYTES:
        raise InventoryError("inventory exceeds the 10 MiB safety limit")
    return body


def _mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise InventoryError(f"{field} must be a JSON object with string keys")
    return value


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise InventoryError(f"{field} must be a nonempty string")
    return value


def _number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise InventoryError(f"{field} must be numeric")
    return float(value)


def _date_range(first: date, last: date) -> tuple[date, ...]:
    return tuple(first + timedelta(days=offset) for offset in range((last - first).days + 1))


def build_inventory_lock(
    body: bytes,
    *,
    inventory_url: str = INVENTORY_URL,
) -> dict[str, object]:
    """Validate one exact snapshot and return the canonical boundary-aware 2024 lock."""

    _validate_inventory_url(inventory_url)
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InventoryError("inventory is not valid UTF-8 JSON") from error
    root = _mapping(payload, "inventory")
    generated_at_raw = _string(root.get("generated_at"), "generated_at")
    try:
        generated_at = datetime.fromisoformat(generated_at_raw)
    except ValueError as error:
        raise InventoryError("generated_at is not ISO-8601") from error
    if generated_at.tzinfo is None:
        raise InventoryError("generated_at must be timezone-aware")
    generated_at = generated_at.astimezone(UTC)

    feeds = _mapping(root.get("feeds"), "feeds")
    feed = _mapping(feeds.get(FEED_ID), f"feeds.{FEED_ID}")
    dates = _mapping(feed.get("dates"), f"feeds.{FEED_ID}.dates")
    snapshot_sha256 = _sha256(body)

    entries: list[InventoryLockEntry] = []
    for inventory_date in _date_range(FIRST_BOUNDARY_DATE, LAST_BOUNDARY_DATE):
        date_key = inventory_date.isoformat()
        objects = dates.get(date_key)
        if not isinstance(objects, list) or len(objects) != 1:
            raise InventoryError(f"inventory date {date_key} must contain exactly one object")
        item = _mapping(objects[0], f"feeds.{FEED_ID}.dates.{date_key}[0]")
        source_url = _string(item.get("url"), f"{date_key}.url")
        parsed = urlparse(source_url)
        entry = InventoryLockEntry(
            inventory_snapshot_url=inventory_url,
            inventory_snapshot_sha256=snapshot_sha256,
            inventory_generated_at=generated_at,
            inventory_date=inventory_date,
            source_object_key=parsed.path.removeprefix("/"),
            source_url=source_url,
            declared_size_mb=_number(item.get("size_mb"), f"{date_key}.size_mb"),
        )
        entries.append(entry)

    entries.sort(key=lambda entry: (entry.inventory_date, entry.source_object_key.encode()))
    if len(entries) != EXPECTED_OBJECT_COUNT:
        raise InventoryError(f"expected {EXPECTED_OBJECT_COUNT} locked objects")
    core_count = sum(FIRST_CORE_DATE <= entry.inventory_date <= LAST_CORE_DATE for entry in entries)
    if core_count != 366:
        raise InventoryError("expected exactly 366 calendar-year objects")

    return {
        "acceptance_version": DEFAULT_ACCEPTANCE_VERSION,
        "lock_version": LOCK_VERSION,
        "feed_id": FEED_ID,
        "inventory_snapshot": {
            "url": inventory_url,
            "sha256": snapshot_sha256,
            "generated_at": generated_at,
        },
        "selection": {
            "first_boundary_date": FIRST_BOUNDARY_DATE,
            "first_core_date": FIRST_CORE_DATE,
            "last_core_date": LAST_CORE_DATE,
            "last_boundary_date": LAST_BOUNDARY_DATE,
        },
        "summary": {
            "object_count": len(entries),
            "core_object_count": core_count,
            "boundary_object_count": len(entries) - core_count,
            "declared_size_mb": round(sum(entry.declared_size_mb for entry in entries), 2),
        },
        "entries": [asdict(entry) for entry in entries],
    }


def _write_immutable(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != body:
            raise InventoryError(f"immutable output already exists with different bytes: {path}")
        return
    descriptor, temporary_name = tempfile.mkstemp(prefix="inventory-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_inventory_lock(
    body: bytes,
    *,
    snapshot_directory: Path,
    lock_path: Path,
    inventory_url: str = INVENTORY_URL,
) -> tuple[Path, str]:
    """Persist the exact snapshot and its deterministic derived lock."""

    snapshot_sha256 = _sha256(body)
    snapshot_path = snapshot_directory / f"{snapshot_sha256}.json"
    lock = build_inventory_lock(body, inventory_url=inventory_url)
    lock_body = canonical_json_bytes(lock)
    _write_immutable(snapshot_path, body)
    _write_immutable(lock_path, lock_body)
    return snapshot_path, _sha256(lock_body)
