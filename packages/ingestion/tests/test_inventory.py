from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from arrive90_ingestion.cli import main
from arrive90_ingestion.inventory import (
    EXPECTED_OBJECT_COUNT,
    FIRST_BOUNDARY_DATE,
    INVENTORY_URL,
    InventoryError,
    build_inventory_lock,
    download_inventory,
    write_inventory_lock,
)


def _inventory_payload() -> dict[str, Any]:
    dates: dict[str, list[dict[str, object]]] = {}
    for offset in range(EXPECTED_OBJECT_COUNT):
        current = FIRST_BOUNDARY_DATE + timedelta(days=offset)
        stamp = f"{current.isoformat()}_13:42:00"
        dates[current.isoformat()] = [
            {
                "timestamp": stamp,
                "size_mb": 1.0 + offset / 100,
                "url": (
                    "https://busobservatory-lake.s3.amazonaws.com/feeds/mbta_all/"
                    f"COMPACTED_mbta_all_{stamp}.parquet"
                ),
            }
        ]
    return {
        "generated_at": "2026-08-14T05:00:51+00:00",
        "feeds": {"mbta_all": {"dates": dates}},
    }


def _inventory_bytes() -> bytes:
    return json.dumps(_inventory_payload(), sort_keys=True).encode()


def test_inventory_lock_is_boundary_aware_complete_and_deterministic() -> None:
    body = _inventory_bytes()
    first = build_inventory_lock(body)
    second = build_inventory_lock(body)
    assert first == second
    assert first["summary"] == {
        "object_count": 368,
        "core_object_count": 366,
        "boundary_object_count": 2,
        "declared_size_mb": 1043.28,
    }
    entries = first["entries"]
    assert isinstance(entries, list)
    assert entries[0]["inventory_date"] == date(2023, 12, 31)
    assert entries[-1]["inventory_date"] == date(2025, 1, 1)


def test_inventory_lock_rejects_missing_or_duplicate_dates() -> None:
    payload = _inventory_payload()
    dates = payload["feeds"]["mbta_all"]["dates"]
    missing = dates.pop("2024-05-15")
    with pytest.raises(InventoryError, match="2024-05-15"):
        build_inventory_lock(json.dumps(payload).encode())
    dates["2024-05-15"] = [*missing, *missing]
    with pytest.raises(InventoryError, match="2024-05-15"):
        build_inventory_lock(json.dumps(payload).encode())


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"{", "valid UTF-8 JSON"),
        (b"[]", "inventory must be a JSON object"),
        (b'{"generated_at":"bad","feeds":{}}', "generated_at is not ISO-8601"),
        (b'{"generated_at":"2026-08-14T05:00:51","feeds":{}}', "timezone-aware"),
    ],
)
def test_inventory_lock_rejects_malformed_top_level_contract(body: bytes, message: str) -> None:
    with pytest.raises(InventoryError, match=message):
        build_inventory_lock(body)


def test_inventory_lock_outputs_are_immutable_and_cli_reports_hash(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body = _inventory_bytes()
    input_path = tmp_path / "inventory.json"
    input_path.write_bytes(body)
    snapshots = tmp_path / "snapshots"
    lock = tmp_path / "lock.json"

    assert (
        main(
            [
                "source",
                "lock",
                "--inventory-file",
                str(input_path),
                "--snapshot-directory",
                str(snapshots),
                "--output",
                str(lock),
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert Path(result["lock_path"]) == lock
    assert len(result["lock_sha256"]) == 64
    assert Path(result["snapshot_path"]).read_bytes() == body

    first_lock = lock.read_bytes()
    write_inventory_lock(body, snapshot_directory=snapshots, lock_path=lock)
    assert lock.read_bytes() == first_lock
    with pytest.raises(InventoryError, match="different bytes"):
        lock.write_text("different", encoding="utf-8")
        write_inventory_lock(body, snapshot_directory=snapshots, lock_path=lock)


def test_inventory_download_rejects_noncanonical_url_before_network() -> None:
    with pytest.raises(InventoryError, match="canonical"):
        download_inventory("https://example.com/inventory.json")
    assert INVENTORY_URL.startswith("https://")
