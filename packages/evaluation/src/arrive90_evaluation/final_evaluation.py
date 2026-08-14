"""One-way Milestone 4 final evaluation and prediction-only report rebuild."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from arrive90_data_contracts.gates import DEFAULT_ACCEPTANCE_VERSION

from arrive90_evaluation.final_artifacts import (
    PredictionArtifact,
    build_replay_selection,
    copy_promoted_bundle,
    evaluation_code_sha256,
    file_sha256,
    load_empirical_baseline,
    load_model_registry,
    load_prediction_artifact,
    value_sha256,
    write_content_addressed_json,
    write_prediction_artifact,
    write_pretty_json,
    write_replay_artifacts,
)
from arrive90_evaluation.final_data import (
    FinalTestAccess,
    load_final_feature_inventory,
    open_final_outcomes,
)
from arrive90_evaluation.final_metrics import predict_final_bundle
from arrive90_evaluation.final_report import build_claim_registry, build_final_report
from arrive90_evaluation.modeling_data import load_modeling_context
from arrive90_evaluation.year_dataset import DEFAULT_DATASET_ROOT, YearDatasetError

ROOT = Path(__file__).resolve().parents[4]
DEFAULT_CONFIG = Path("configs/evaluation/travel-time-v1.json")
DEFAULT_MODEL_ROOT = Path("data/models/travel-time-v1/primary")
DEFAULT_NORMALIZED_ROOT = Path("data/normalized")
DEFAULT_RUNTIME_ROOT = Path("artifacts/runtime/milestone-4")
DEFAULT_DEMO_ROOT = Path("artifacts/demo/travel-time-v1")
DEFAULT_FINAL_REPORT = Path("artifacts/reports/final/travel-time-v1.2.json")
DEFAULT_CLAIM_REGISTRY = Path("artifacts/reports/claims/travel-time-v1.2.json")
DEFAULT_SCHEDULE_DATABASE = Path("data/raw/mbta-gtfs/2024/GTFS_ARCHIVE.db")
DEFAULT_MILESTONE_THREE_GATE = Path("artifacts/reports/gates/milestone-3.json")
EVALUATION_SOURCE_NAMES = (
    "final_artifacts.py",
    "final_bootstrap.py",
    "final_data.py",
    "final_evaluation.py",
    "final_metrics.py",
    "final_report.py",
)


@dataclass(frozen=True, slots=True)
class FinalEvaluationResult:
    protocol_path: Path
    protocol_sha256: str
    prediction_artifact: PredictionArtifact
    final_report_path: Path
    final_report_sha256: str
    claim_registry_path: Path
    claim_registry_sha256: str
    replay_fixture_sha256: str
    runtime_report_path: Path


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise YearDatasetError(f"{path} must contain a JSON object")
    return payload


def _load_config(path: Path) -> dict[str, Any]:
    payload = _load_json(path)
    if (
        payload.get("acceptance_version") != DEFAULT_ACCEPTANCE_VERSION
        or payload.get("version") != "travel-time-final-evaluation-v1"
        or payload.get("horizons_seconds") != [300, 600, 900, 1200, 1800, 2700, 3600]
        or payload.get("quantiles") != [0.5, 0.8, 0.9]
        or payload.get("model_horizon_seconds") != 3600
        or payload.get("bootstrap", {}).get("replicates") != 2000
        or payload.get("bootstrap", {}).get("unit") != "COMPLETE_SERVICE_DATE"
        or payload.get("bootstrap", {}).get("quantile_method") != "linear"
    ):
        raise YearDatasetError("final evaluation configuration violates the frozen protocol")
    return payload


def _project_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _frozen_protocol(
    *,
    config: dict[str, Any],
    config_path: Path,
    context: Any,
    registry: Any,
    replay_selection_sha256: str,
    milestone_three_gate_path: Path,
) -> dict[str, Any]:
    gate = _load_json(milestone_three_gate_path)
    if (
        gate.get("acceptance_version") != DEFAULT_ACCEPTANCE_VERSION
        or gate.get("milestone") != 3
        or gate.get("state") != "ACCEPTED"
        or gate.get("failing_checks") != []
    ):
        raise YearDatasetError("Milestone 3 must be accepted before final evaluation")
    source_root = Path(__file__).resolve().parent
    source_paths = tuple(source_root / name for name in EVALUATION_SOURCE_NAMES)
    code_sha256 = evaluation_code_sha256(source_paths, root=ROOT)
    return {
        "acceptance_version": DEFAULT_ACCEPTANCE_VERSION,
        "bootstrap": config["bootstrap"],
        "calibration": config["calibration"],
        "claim_templates": config["claim_templates"],
        "dataset_manifest_sha256": context.population_manifest_sha256,
        "dependency_lock_sha256": file_sha256(ROOT / "uv.lock"),
        "evaluation_code_sha256": code_sha256,
        "evaluation_config_sha256": file_sha256(config_path),
        "feature_registry_sha256": registry.promoted.manifest.feature_registry_sha256,
        "feature_transform_sha256": context.feature_transform_sha256,
        "final_test": config["final_test"],
        "final_test_outcomes_opened": False,
        "horizons_seconds": config["horizons_seconds"],
        "metric_names": [
            "weighted calibrated interval negative log likelihood",
            "identified and complete-population Brier bounds",
            "calibration ECE, MCE, and success-rate bounds",
            "median absolute interval distance",
            "interval-aware pinball bounds",
            "empirical quantile coverage bounds",
            "p50 to p90 prediction interval width",
            "outcome-state retained mass",
        ],
        "milestone_three_gate_sha256": file_sha256(milestone_three_gate_path),
        "model_horizon_seconds": config["model_horizon_seconds"],
        "model_registry_index_sha256": registry.index_sha256,
        "model_selection_freeze_sha256": registry.index["selection_freeze_sha256"],
        "model_order": [entry["bundle_id"] for entry in registry.index["entries"]],
        "promoted_bundle_id": registry.promoted_bundle_id,
        "promoted_manifest_sha256": registry.promoted.manifest.manifest_hash,
        "quantiles": config["quantiles"],
        "replay_selection_sha256": replay_selection_sha256,
        "slice_dimensions": config["slice_dimensions"],
        "split_manifest_sha256": context.split_manifest_sha256,
        "unsampled_manifest_sha256": context.unsampled_manifest_sha256,
        "version": "travel-time-evaluation-freeze-v1",
    }


def _access_ledger(
    runtime_root: Path,
    *,
    protocol_sha256: str,
    replay_selection_sha256: str,
) -> Path:
    path = runtime_root / "final-test-access.json"
    if path.exists():
        raise YearDatasetError(
            "final-test access already occurred for this runtime; rebuild from predictions"
        )
    write_pretty_json(
        path,
        {
            "acceptance_version": DEFAULT_ACCEPTANCE_VERSION,
            "access_count": 1,
            "final_test_outcomes_opened": True,
            "protocol_sha256": protocol_sha256,
            "replay_selection_sha256": replay_selection_sha256,
            "requesting_milestone": 4,
            "version": "travel-time-final-access-v1",
        },
    )
    return path


def run_final_evaluation(
    *,
    dataset_root: Path = DEFAULT_DATASET_ROOT,
    normalized_root: Path = DEFAULT_NORMALIZED_ROOT,
    model_root: Path = DEFAULT_MODEL_ROOT,
    config_path: Path = DEFAULT_CONFIG,
    schedule_database: Path = DEFAULT_SCHEDULE_DATABASE,
    runtime_root: Path = DEFAULT_RUNTIME_ROOT,
    demo_root: Path = DEFAULT_DEMO_ROOT,
    final_report_path: Path = DEFAULT_FINAL_REPORT,
    claim_registry_path: Path = DEFAULT_CLAIM_REGISTRY,
    milestone_three_gate_path: Path = DEFAULT_MILESTONE_THREE_GATE,
) -> FinalEvaluationResult:
    """Freeze, open final outcomes once, score, and package all Milestone 4 evidence."""

    started = time.monotonic()
    runtime_root.mkdir(parents=True, exist_ok=True)
    if any(runtime_root.iterdir()):
        raise YearDatasetError(
            "Milestone 4 runtime must be empty before the one-way final evaluation"
        )
    config = _load_config(config_path)
    context = load_modeling_context(dataset_root, normalized_root=normalized_root)
    inventory = load_final_feature_inventory(context)
    registry = load_model_registry(model_root, full_feature_count=inventory.features.shape[1])
    empirical, point_baseline_sha256 = load_empirical_baseline(model_root)
    model_order = tuple(str(entry["bundle_id"]) for entry in registry.index["entries"])
    predictions = {
        bundle_id: predict_final_bundle(
            registry.bundles[bundle_id],
            inventory.features,
            horizons_seconds=tuple(int(value) for value in config["horizons_seconds"]),
            quantiles=tuple(float(value) for value in config["quantiles"]),
            model_horizon_seconds=int(config["model_horizon_seconds"]),
        )
        for bundle_id in model_order
    }
    replay_selection = build_replay_selection(
        inventory,
        registry.promoted,
        schedule_database=schedule_database,
        replay_config=config["replay"],
    )
    replay_selection_path, replay_selection_sha256 = write_content_addressed_json(
        runtime_root, "replay-selection-freeze", replay_selection.manifest
    )
    if replay_selection_sha256 != replay_selection.manifest_sha256:
        raise YearDatasetError("replay selection freeze hash is inconsistent")
    protocol = _frozen_protocol(
        config=config,
        config_path=config_path,
        context=context,
        registry=registry,
        replay_selection_sha256=replay_selection_sha256,
        milestone_three_gate_path=milestone_three_gate_path,
    )
    protocol_path, protocol_sha256 = write_content_addressed_json(
        runtime_root, "evaluation-freeze", protocol
    )
    _access_ledger(
        runtime_root,
        protocol_sha256=protocol_sha256,
        replay_selection_sha256=replay_selection_sha256,
    )
    data = open_final_outcomes(
        inventory,
        FinalTestAccess(protocol_sha256, replay_selection_sha256),
    )
    prediction_artifact = write_prediction_artifact(
        runtime_root,
        data,
        predictions,
        empirical,
        protocol_sha256=protocol_sha256,
        replay_selection_sha256=replay_selection_sha256,
        model_order=model_order,
    )
    bundle_directory, demo_bundle_sha256, demo_bundle_bytes = copy_promoted_bundle(
        registry,
        demo_root,
        size_limit_bytes=10 * 1024 * 1024,
    )
    replay_artifacts = write_replay_artifacts(
        demo_root,
        replay_selection,
        data,
        predictions[registry.promoted_bundle_id],
        forbidden_fields=tuple(
            str(value) for value in config["replay"]["forbidden_fixture_fields"]
        ),
    )
    demo_artifacts = {
        "bundle_bytes": demo_bundle_bytes,
        "bundle_directory": _project_path(bundle_directory),
        "bundle_manifest_sha256": registry.promoted.manifest.manifest_hash,
        "bundle_tree_sha256": demo_bundle_sha256,
        **{
            key: (_project_path(Path(value)) if key.endswith("_path") else value)
            for key, value in replay_artifacts.items()
        },
    }
    frozen_manifest, frozen_table = load_prediction_artifact(prediction_artifact.manifest_path)
    report = build_final_report(
        context,
        config,
        registry,
        frozen_manifest,
        frozen_table,
        protocol=protocol,
        demo_artifacts=demo_artifacts,
    )
    final_report_sha256 = write_pretty_json(final_report_path, report)
    claims = build_claim_registry(report, final_report_sha256)
    claim_registry_sha256 = write_pretty_json(claim_registry_path, claims)
    runtime_report = {
        "acceptance_version": DEFAULT_ACCEPTANCE_VERSION,
        "claim_registry_path": _project_path(claim_registry_path),
        "claim_registry_sha256": claim_registry_sha256,
        "elapsed_seconds": time.monotonic() - started,
        "final_report_path": _project_path(final_report_path),
        "final_report_sha256": final_report_sha256,
        "final_test_access_count": 1,
        "point_baseline_sha256": point_baseline_sha256,
        "prediction_manifest_path": _project_path(prediction_artifact.manifest_path),
        "prediction_manifest_sha256": prediction_artifact.manifest_sha256,
        "protocol_path": _project_path(protocol_path),
        "protocol_sha256": protocol_sha256,
        "replay_selection_path": _project_path(replay_selection_path),
        "replay_selection_sha256": replay_selection_sha256,
        "row_count": len(inventory.rows),
        "version": "travel-time-final-evaluation-run-v1",
    }
    runtime_report_path = runtime_root / "evaluation-run.json"
    write_pretty_json(runtime_report_path, runtime_report)
    return FinalEvaluationResult(
        protocol_path,
        protocol_sha256,
        prediction_artifact,
        final_report_path,
        final_report_sha256,
        claim_registry_path,
        claim_registry_sha256,
        str(replay_artifacts["fixture_sha256"]),
        runtime_report_path,
    )


def rebuild_final_report(
    *,
    prediction_manifest_path: Path,
    protocol_path: Path,
    existing_report_path: Path = DEFAULT_FINAL_REPORT,
    output_path: Path,
    dataset_root: Path = DEFAULT_DATASET_ROOT,
    normalized_root: Path = DEFAULT_NORMALIZED_ROOT,
    model_root: Path = DEFAULT_MODEL_ROOT,
    config_path: Path = DEFAULT_CONFIG,
) -> str:
    """Rebuild deterministic final tables without reading a sealed outcome partition."""

    config = _load_config(config_path)
    protocol = _load_json(protocol_path)
    if value_sha256(protocol) != protocol_path.stem.removeprefix("evaluation-freeze-"):
        raise YearDatasetError("evaluation protocol path is not content addressed")
    context = load_modeling_context(dataset_root, normalized_root=normalized_root)
    registry = load_model_registry(
        model_root, full_feature_count=len(context.feature_transform.column_names)
    )
    manifest, table = load_prediction_artifact(prediction_manifest_path)
    existing = _load_json(existing_report_path)
    report = build_final_report(
        context,
        config,
        registry,
        manifest,
        table,
        protocol=protocol,
        demo_artifacts=existing["demo_artifacts"],
    )
    return write_pretty_json(output_path, report)
