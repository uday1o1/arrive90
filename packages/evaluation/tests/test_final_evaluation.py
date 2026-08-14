from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pytest
from arrive90_evaluation import final_evaluation
from arrive90_evaluation.final_artifacts import (
    PredictionArtifact,
    ReplaySelection,
    value_sha256,
    write_content_addressed_json,
)
from arrive90_evaluation.year_dataset import YearDatasetError


def _sha(character: str) -> str:
    return character * 64


def _config() -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads(Path("configs/evaluation/travel-time-v1.json").read_text(encoding="utf-8")),
    )


def _registry() -> SimpleNamespace:
    promoted_manifest = SimpleNamespace(
        feature_registry_sha256=_sha("1"),
        manifest_hash=_sha("2"),
    )
    promoted = SimpleNamespace(manifest=promoted_manifest)
    return SimpleNamespace(
        bundles={"FULL-normal-scale-0p5": promoted},
        index={
            "entries": [{"bundle_id": "FULL-normal-scale-0p5"}],
            "selection_freeze_sha256": _sha("3"),
        },
        index_sha256=_sha("4"),
        promoted=promoted,
        promoted_bundle_id="FULL-normal-scale-0p5",
    )


def _context(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        dataset_root=tmp_path / "dataset",
        feature_transform=SimpleNamespace(column_names=("feature",)),
        feature_transform_sha256=_sha("5"),
        population_manifest_sha256=_sha("6"),
        split_manifest_sha256=_sha("7"),
        unsampled_manifest_sha256=_sha("8"),
    )


def test_final_freeze_config_protocol_and_access_ledger_fail_closed(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(_config()), encoding="utf-8")
    assert final_evaluation._load_config(config_path)["bootstrap"]["replicates"] == 2000
    config_path.write_text("[]", encoding="utf-8")
    with pytest.raises(YearDatasetError, match="JSON object"):
        final_evaluation._load_json(config_path)
    config_path.write_text(json.dumps({"acceptance_version": "bad"}), encoding="utf-8")
    with pytest.raises(YearDatasetError, match="frozen protocol"):
        final_evaluation._load_config(config_path)

    config_path.write_text(json.dumps(_config()), encoding="utf-8")
    gate_path = tmp_path / "gate.json"
    gate_path.write_text(
        json.dumps(
            {
                "acceptance_version": "travel-time-v1.2",
                "failing_checks": [],
                "milestone": 3,
                "state": "ACCEPTED",
            }
        ),
        encoding="utf-8",
    )
    protocol = final_evaluation._frozen_protocol(
        config=_config(),
        config_path=config_path,
        context=_context(tmp_path),
        registry=_registry(),
        replay_selection_sha256=_sha("9"),
        milestone_three_gate_path=gate_path,
    )
    assert protocol["final_test_outcomes_opened"] is False
    assert protocol["promoted_bundle_id"] == "FULL-normal-scale-0p5"

    gate_path.write_text(json.dumps({"state": "FAILED"}), encoding="utf-8")
    with pytest.raises(YearDatasetError, match="Milestone 3"):
        final_evaluation._frozen_protocol(
            config=_config(),
            config_path=config_path,
            context=_context(tmp_path),
            registry=_registry(),
            replay_selection_sha256=_sha("9"),
            milestone_three_gate_path=gate_path,
        )

    ledger = final_evaluation._access_ledger(
        tmp_path / "runtime",
        protocol_sha256=_sha("a"),
        replay_selection_sha256=_sha("b"),
    )
    assert json.loads(ledger.read_text())["access_count"] == 1
    with pytest.raises(YearDatasetError, match="already occurred"):
        final_evaluation._access_ledger(
            tmp_path / "runtime",
            protocol_sha256=_sha("a"),
            replay_selection_sha256=_sha("b"),
        )


def test_run_final_evaluation_preserves_one_way_order_and_writes_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path)
    registry = _registry()
    inventory = SimpleNamespace(features=np.zeros((3, 1)), rows=(1, 2, 3))
    prediction = SimpleNamespace(bundle_id="FULL-normal-scale-0p5")
    replay = ReplaySelection(
        (0,),
        {"entries": [], "final_test_outcomes_opened": False},
        value_sha256({"entries": [], "final_test_outcomes_opened": False}),
        {"coordinates": {}},
    )
    data = SimpleNamespace(inventory=inventory)
    empirical = SimpleNamespace()
    calls: list[str] = []

    monkeypatch.setattr(final_evaluation, "load_modeling_context", lambda *_a, **_k: context)
    monkeypatch.setattr(
        final_evaluation, "load_final_feature_inventory", lambda _context: inventory
    )
    monkeypatch.setattr(
        final_evaluation,
        "load_model_registry",
        lambda *_a, **_k: registry,
    )
    monkeypatch.setattr(
        final_evaluation,
        "load_empirical_baseline",
        lambda _root: (empirical, _sha("c")),
    )
    monkeypatch.setattr(
        final_evaluation,
        "predict_final_bundle",
        lambda *_a, **_k: prediction,
    )
    monkeypatch.setattr(
        final_evaluation,
        "build_replay_selection",
        lambda *_a, **_k: replay,
    )

    def open_outcomes(_inventory: object, _access: object) -> object:
        calls.append("outcomes")
        return data

    monkeypatch.setattr(final_evaluation, "open_final_outcomes", open_outcomes)
    runtime = tmp_path / "runtime"
    prediction_path = runtime / "prediction.parquet"
    prediction_manifest_path = runtime / "prediction-manifest.json"

    def write_predictions(*_args: object, **_kwargs: object) -> PredictionArtifact:
        assert calls == ["outcomes"]
        calls.append("predictions")
        prediction_path.write_bytes(b"prediction")
        prediction_manifest_path.write_text("{}", encoding="utf-8")
        return PredictionArtifact(
            prediction_path,
            _sha("d"),
            prediction_manifest_path,
            _sha("e"),
            {},
        )

    monkeypatch.setattr(final_evaluation, "write_prediction_artifact", write_predictions)
    bundle_directory = tmp_path / "demo/model/bundle"
    monkeypatch.setattr(
        final_evaluation,
        "copy_promoted_bundle",
        lambda *_a, **_k: (bundle_directory, _sha("f"), 123),
    )
    monkeypatch.setattr(
        final_evaluation,
        "write_replay_artifacts",
        lambda *_a, **_k: {
            "fixture_path": (tmp_path / "demo/replay.json").as_posix(),
            "fixture_sha256": _sha("0"),
            "replay_count": 1,
        },
    )
    monkeypatch.setattr(
        final_evaluation,
        "load_prediction_artifact",
        lambda _path: ({"row_count": 3}, pa.table({"value": [1, 2, 3]})),
    )
    monkeypatch.setattr(
        final_evaluation,
        "build_final_report",
        lambda *_a, **_k: {"demo_artifacts": {}, "models": []},
    )
    monkeypatch.setattr(
        final_evaluation,
        "build_claim_registry",
        lambda _report, digest: {"report_sha256": digest},
    )
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(_config()), encoding="utf-8")
    gate_path = tmp_path / "gate.json"
    gate_path.write_text(
        json.dumps(
            {
                "acceptance_version": "travel-time-v1.2",
                "failing_checks": [],
                "milestone": 3,
                "state": "ACCEPTED",
            }
        ),
        encoding="utf-8",
    )
    result = final_evaluation.run_final_evaluation(
        dataset_root=tmp_path / "dataset",
        normalized_root=tmp_path / "normalized",
        model_root=tmp_path / "models",
        config_path=config_path,
        schedule_database=tmp_path / "schedule.db",
        runtime_root=runtime,
        demo_root=tmp_path / "demo",
        final_report_path=tmp_path / "final-report.json",
        claim_registry_path=tmp_path / "claims.json",
        milestone_three_gate_path=gate_path,
    )
    assert calls == ["outcomes", "predictions"]
    assert result.replay_fixture_sha256 == _sha("0")
    run_report = json.loads(result.runtime_report_path.read_text())
    assert run_report["final_test_access_count"] == 1
    assert run_report["row_count"] == 3

    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "existing").touch()
    with pytest.raises(YearDatasetError, match="must be empty"):
        final_evaluation.run_final_evaluation(runtime_root=occupied)


def test_prediction_only_report_rebuild_verifies_protocol_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protocol_path, _ = write_content_addressed_json(
        tmp_path, "evaluation-freeze", {"version": "freeze"}
    )
    existing_report = tmp_path / "existing.json"
    existing_report.write_text(json.dumps({"demo_artifacts": {"fixture": "hash"}}))
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(_config()))
    context = _context(tmp_path)
    monkeypatch.setattr(final_evaluation, "load_modeling_context", lambda *_a, **_k: context)
    monkeypatch.setattr(final_evaluation, "load_model_registry", lambda *_a, **_k: _registry())
    monkeypatch.setattr(
        final_evaluation,
        "load_prediction_artifact",
        lambda _path: ({"row_count": 1}, pa.table({"value": [1]})),
    )
    monkeypatch.setattr(
        final_evaluation,
        "build_final_report",
        lambda *_a, **kwargs: {"demo_artifacts": kwargs["demo_artifacts"]},
    )
    output = tmp_path / "rebuilt.json"
    digest = final_evaluation.rebuild_final_report(
        prediction_manifest_path=tmp_path / "predictions.json",
        protocol_path=protocol_path,
        existing_report_path=existing_report,
        output_path=output,
        dataset_root=tmp_path / "dataset",
        normalized_root=tmp_path / "normalized",
        model_root=tmp_path / "models",
        config_path=config_path,
    )
    assert len(digest) == 64
    assert json.loads(output.read_text())["demo_artifacts"] == {"fixture": "hash"}

    bad_protocol = tmp_path / "evaluation-freeze-bad.json"
    bad_protocol.write_text('{"version":"freeze"}')
    with pytest.raises(YearDatasetError, match="content addressed"):
        final_evaluation.rebuild_final_report(
            prediction_manifest_path=tmp_path / "predictions.json",
            protocol_path=bad_protocol,
            output_path=tmp_path / "bad.json",
            config_path=config_path,
        )
