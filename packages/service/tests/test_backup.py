from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from arrive90_service.backup import create_backup, restore_backup
from arrive90_service.contracts import ServiceConfig
from arrive90_service.store import CapabilityTripStore


def _config() -> ServiceConfig:
    return ServiceConfig(
        allowed_hosts=frozenset({"testserver"}),
        allowed_origins=frozenset({"http://testserver"}),
        decision_keys=(("d1", b"d" * 32),),
        active_decision_key_version="d1",
        trip_keys=(("t1", b"t" * 32),),
        active_trip_key_version="t1",
    )


def _snapshot(selected: str) -> dict[str, object]:
    return {
        "candidate_generator_version": "STATIC_ROUTE_POLICY_V1",
        "data_cutoff": "2025-01-01T12:00:00Z",
        "decision_context_id": "context",
        "feature_schema_version": "historical_v1",
        "feed_status": "FRESH",
        "model_version": "model-v1",
        "selected_itinerary": {
            "allowed_boarding_ids": [selected],
            "itinerary_id": selected,
            "transfer_count": 0,
        },
        "source_attempt_lineage": ["attempt"],
        "static_candidate_manifest_hash": "manifest",
    }


def test_backup_restore_is_create_only_integral_and_authorization_preserving(
    tmp_path: Path,
) -> None:
    config = _config()
    state = tmp_path / "state.sqlite3"
    store = CapabilityTripStore(state, config)
    selected = "a" * 64
    issued = store.issue_decision(_snapshot(selected), recommended_itinerary_id=selected, now=100)
    created = store.consume_and_create_trip(
        issued.capability,
        selected_itinerary_id=selected,
        now=101,
    )
    backup = tmp_path / "backup.sqlite3"
    manifest_path = tmp_path / "backup.json"
    manifest = create_backup(
        state,
        backup,
        manifest_path,
        created_at_utc="2025-01-01T12:00:00Z",
        expire_before_epoch=102,
    )
    assert manifest.row_counts["trips"] == 1
    assert manifest.plaintext_secrets_stored is False
    assert manifest.rider_identity_or_coordinates_stored is False
    assert os.stat(backup).st_mode & 0o077 == 0
    with pytest.raises(FileExistsError):
        create_backup(
            state,
            backup,
            manifest_path,
            created_at_utc="2025-01-01T12:00:00Z",
            expire_before_epoch=102,
        )
    restored = tmp_path / "restored.sqlite3"
    assert restore_backup(backup, manifest_path, restored) == manifest
    restored_store = CapabilityTripStore(restored, config)
    assert restored_store.authorize_trip(created.trip_id, created.bearer, now=103).trip_id == (
        created.trip_id
    )
    restored_store.close()
    store.close()


def test_restore_rejects_tampering_schema_and_overwrite(tmp_path: Path) -> None:
    state = tmp_path / "state.sqlite3"
    store = CapabilityTripStore(state, _config())
    backup = tmp_path / "backup.sqlite3"
    manifest = tmp_path / "backup.json"
    create_backup(
        state,
        backup,
        manifest,
        created_at_utc="2025-01-01T12:00:00Z",
        expire_before_epoch=0,
    )
    destination = tmp_path / "destination.sqlite3"
    destination.write_bytes(b"occupied")
    with pytest.raises(FileExistsError):
        restore_backup(backup, manifest, destination)
    destination.unlink()
    bad_manifest = tmp_path / "bad-manifest.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["schema_version"] = "future"
    bad_manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported"):
        restore_backup(backup, bad_manifest, destination)
    payload["schema_version"] = "arrive90-state-backup-v1"
    payload["plaintext_secrets_stored"] = True
    bad_manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="sensitive-data"):
        restore_backup(backup, bad_manifest, destination)
    raw = bytearray(backup.read_bytes())
    raw[-1] ^= 1
    backup.write_bytes(raw)
    with pytest.raises(ValueError, match="digest"):
        restore_backup(backup, manifest, destination)
    store.close()


def test_backup_rejects_missing_source_and_non_utc_manifest_time(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="explicit UTC"):
        create_backup(
            tmp_path / "missing.sqlite3",
            tmp_path / "backup.sqlite3",
            tmp_path / "backup.json",
            created_at_utc="2025-01-01T12:00:00",
            expire_before_epoch=0,
        )
    with pytest.raises(FileNotFoundError, match="does not exist"):
        create_backup(
            tmp_path / "missing.sqlite3",
            tmp_path / "backup.sqlite3",
            tmp_path / "backup.json",
            created_at_utc="2025-01-01T12:00:00Z",
            expire_before_epoch=0,
        )


def test_public_backup_and_restore_cli_path(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[3]
    state = tmp_path / "state.sqlite3"
    CapabilityTripStore(state, _config()).close()
    backup = tmp_path / "backup.sqlite3"
    manifest = tmp_path / "backup.json"
    restored = tmp_path / "restored.sqlite3"

    def run(*arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - exact repository CLI under test
            [sys.executable, str(root / "scripts/manage_state.py"), *arguments],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )

    created = run(
        "backup",
        "--state",
        str(state),
        "--output",
        str(backup),
        "--manifest",
        str(manifest),
        "--created-at-utc",
        "2025-01-01T12:00:00Z",
        "--expire-before-epoch",
        "0",
    )
    assert created.returncode == 0, created.stderr
    restored_result = run(
        "restore",
        "--backup",
        str(backup),
        "--manifest",
        str(manifest),
        "--output",
        str(restored),
    )
    assert restored_result.returncode == 0, restored_result.stderr
    assert created.stdout.strip() == restored_result.stdout.strip()
    assert restored.is_file()
