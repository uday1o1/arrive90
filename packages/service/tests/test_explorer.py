from __future__ import annotations

import json
import shutil
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from arrive90_service.explorer import (
    HORIZONS,
    ExplorerArtifactError,
    ExplorerRepository,
    ReplayRecord,
)


@pytest.fixture(scope="module")
def repository() -> ExplorerRepository:
    return ExplorerRepository.load()


def _contains_key(value: Any, forbidden: set[str]) -> bool:
    if isinstance(value, dict):
        return bool(forbidden.intersection(value)) or any(
            _contains_key(item, forbidden) for item in value.values()
        )
    if isinstance(value, list):
        return any(_contains_key(item, forbidden) for item in value)
    return False


def test_repository_verifies_exact_frozen_assets(repository: ExplorerRepository) -> None:
    metadata = repository.metadata()

    assert metadata["acceptance_version"] == "travel-time-v1.2"
    assert metadata["replay_count"] == 200
    assert metadata["retained_lines"] == [{"line_id": "Blue", "name": "Blue Line"}]
    assert metadata["final_test"]["row_count"] == 199_364
    assert metadata["model"]["bundle_id"] == "FULL-normal-scale-0p5"
    assert metadata["artifact_hashes"]["model_manifest"] == repository.bundle.manifest.manifest_hash
    assert (
        metadata["point_diagnostics"]["models"]["PROMOTED_P50"]["metric_eligible"]["raw_row_count"]
        == 157_112
    )
    assert repository.lines()["lines"][0]["line_id"] == "Blue"
    assert repository.stations()["stations"]


def test_inventory_filters_real_replays(repository: ExplorerRepository) -> None:
    all_rows = repository.inventory()["replays"]
    selected = all_rows[0]
    filtered = repository.inventory(
        direction_id=selected["direction_id"],
        origin_stop_id=selected["origin"]["stop_id"],
        destination_stop_id=selected["destination"]["stop_id"],
    )

    assert len(all_rows) == 200
    assert filtered["replays"]
    assert all(row["line_id"] == "Blue" for row in filtered["replays"])
    assert all(row["origin"] == selected["origin"] for row in filtered["replays"])
    with pytest.raises(ValueError, match="only retained line"):
        repository.inventory(line_id="Orange")


def test_real_scorer_matches_frozen_offline_prediction(repository: ExplorerRepository) -> None:
    replay_id = next(iter(repository.records))
    expected = repository.records[replay_id].payload["offline_prediction"]
    prediction = repository.prediction(replay_id, horizon_seconds=900)

    assert prediction["model"]["raw_margin"] == pytest.approx(expected["raw_margin"], abs=1e-12)
    assert [
        row["probability"] for row in prediction["fixed_horizon_probabilities"]
    ] == pytest.approx(expected["probabilities"], abs=1e-12)
    assert [row["seconds"] for row in prediction["fixed_horizon_probabilities"]] == list(HORIZONS)
    assert [row["level"] for row in prediction["quantiles"]] == ["p50", "p80", "p90"]
    assert prediction["baselines"]["official_schedule"]["seconds"] > 0
    assert "backoff_level" in prediction["baselines"]["empirical_midpoint"]
    assert prediction["selected_horizon"]["seconds"] == 900


def test_prediction_path_excludes_later_outcome(repository: ExplorerRepository) -> None:
    replay_id = next(iter(repository.records))
    prediction = repository.prediction(replay_id, horizon_seconds=300)
    forbidden = {"lower_bound_seconds", "upper_bound_seconds", "outcome_state", "outcome_reveal"}

    assert not _contains_key(prediction, forbidden)
    assert prediction["outcome_data_available_to_scorer"] is False
    reveal = repository.reveal(replay_id)
    assert reveal["observed_after_cutoff"] is True
    assert reveal["outcome"]["outcome_state"] in {
        "INTERVAL_RESOLVED",
        "LEFT_CENSORED",
        "RIGHT_CENSORED",
        "MISSING_STOP_OBSERVATION",
        "NO_FOLLOW_UP",
        "OVER_WIDTH_INTERVAL",
        "SESSION_DISCONTINUITY",
    }


def test_reliability_and_evidence_are_linked_to_final_report(
    repository: ExplorerRepository,
) -> None:
    reliability = repository.reliability(horizon_seconds=1200)
    evidence = repository.evidence()

    assert reliability["horizon_seconds"] == 1200
    assert "claims" in evidence
    assert evidence["report_sha256"] == repository.claims["final_report_sha256"]
    with pytest.raises(ValueError, match="unsupported reliability horizon"):
        repository.reliability(horizon_seconds=1)
    with pytest.raises(KeyError, match="unknown held-out replay"):
        repository.reveal("absent")


def test_corrupted_allow_listed_asset_fails_closed(tmp_path: Path) -> None:
    copied = tmp_path / "demo"
    shutil.copytree("artifacts/demo/travel-time-v1", copied)
    transform = copied / "feature-transform.json"
    payload = json.loads(transform.read_text(encoding="utf-8"))
    payload["version"] = "tampered"
    transform.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ExplorerArtifactError, match="lineage failed verification"):
        ExplorerRepository.load(copied)


def test_corrupted_model_bytes_fail_before_scoring(tmp_path: Path) -> None:
    copied = tmp_path / "demo"
    shutil.copytree("artifacts/demo/travel-time-v1", copied)
    model = next((copied / "model").glob("*/model.ubj"))
    model.write_bytes(model.read_bytes() + b"seeded-corruption")

    with pytest.raises(ExplorerArtifactError, match="model bundle failed validation"):
        ExplorerRepository.load(copied)


def test_missing_coordinate_changed_score_and_missing_reveal_fail_closed(
    repository: ExplorerRepository,
) -> None:
    replay_id = next(iter(repository.records))
    without_coordinate = replace(repository, station_coordinates={})
    with pytest.raises(ExplorerArtifactError, match="station coordinate"):
        without_coordinate.prediction(replay_id, horizon_seconds=900)

    changed_payload = deepcopy(repository.records[replay_id].payload)
    changed_payload["offline_prediction"]["raw_margin"] += 1
    changed_records = dict(repository.records)
    changed_records[replay_id] = ReplayRecord(replay_id, changed_payload)
    with pytest.raises(ExplorerArtifactError, match="differs from frozen offline score"):
        replace(repository, records=changed_records).prediction(replay_id, horizon_seconds=900)

    missing_reveal_payload = deepcopy(repository.records[replay_id].payload)
    missing_reveal_payload.pop("outcome_reveal")
    missing_reveal_records = dict(repository.records)
    missing_reveal_records[replay_id] = ReplayRecord(replay_id, missing_reveal_payload)
    with pytest.raises(ExplorerArtifactError, match="outcome reveal is unavailable"):
        replace(repository, records=missing_reveal_records).reveal(replay_id)
