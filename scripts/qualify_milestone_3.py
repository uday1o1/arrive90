"""Verify the complete travel-time-v1.2 Milestone 3 gate from immutable artifacts."""

from __future__ import annotations

import hashlib
import json
import math
import platform
import shutil
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
from arrive90_data_contracts.dataset import DatasetSplit
from arrive90_data_contracts.gates import DEFAULT_ACCEPTANCE_VERSION
from arrive90_evaluation.aft_metrics import weighted_interval_nll
from arrive90_evaluation.modeling_data import load_modeling_context, load_modeling_split
from arrive90_evaluation.year_dataset import FinalTestOutcomeAccessError, YearDatasetError
from arrive90_features.travel_time_registry import TRAVEL_TIME_V1_REGISTRY
from arrive90_models.distributions import AftDistribution
from arrive90_models.predictive_bundle import AftPredictiveBundle
from arrive90_models.registry import (
    ModelBundleManifest,
    ModelRegistry,
    RegistryExpectations,
)

ROOT = Path(__file__).resolve().parents[1]
PRIMARY_ROOT = ROOT / "data/models/travel-time-v1/primary"
RESTART_ROOT = ROOT / "data/models/travel-time-v1/restart"
PRIMARY_RUNTIME = ROOT / "artifacts/runtime/milestone-3"
RESTART_RUNTIME = ROOT / "artifacts/runtime/milestone-3-restart"
QUALIFICATION_PATH = ROOT / "artifacts/reports/qualification/milestone-3-model-v1.2.json"
GATE_PATH = ROOT / "artifacts/reports/gates/milestone-3.json"
PROMOTED_SIZE_LIMIT = 10 * 1024 * 1024
HORIZONS = (300, 600, 900, 1200, 1800, 2700, 3600)
LATENCY_RESOLUTION_SECONDS = 0.001
_GIT = shutil.which("git")
if _GIT is None:
    raise RuntimeError("git is required for Milestone 3 qualification")
GIT: str = _GIT


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise YearDatasetError(f"{path} must contain a JSON object")
    return payload


def _single(root: Path, pattern: str) -> Path:
    paths = sorted(root.glob(pattern))
    if len(paths) != 1:
        raise YearDatasetError(f"expected one {pattern} artifact under {root}, found {len(paths)}")
    return paths[0]


def _index(root: Path) -> tuple[Path, dict[str, Any], str]:
    path = _single(root, "registry-index-*.json")
    digest = _digest(path)
    if path.stem != f"registry-index-{digest}":
        raise YearDatasetError("registry index filename does not match its content hash")
    payload = _load_json(path)
    if payload.get("acceptance_version") != DEFAULT_ACCEPTANCE_VERSION:
        raise YearDatasetError("registry index acceptance version is invalid")
    return path, payload, digest


def _tree_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): _digest(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _expectations(manifest: ModelBundleManifest) -> RegistryExpectations:
    return RegistryExpectations(
        acceptance_version=manifest.acceptance_version,
        source_lock_sha256=manifest.source_lock_sha256,
        normalized_manifest_sha256=manifest.normalized_manifest_sha256,
        dataset_manifest_sha256=manifest.dataset_manifest_sha256,
        unsampled_manifest_sha256=manifest.unsampled_manifest_sha256,
        split_manifest_sha256=manifest.split_manifest_sha256,
        feature_registry_sha256=manifest.feature_registry_sha256,
        feature_transform_sha256=manifest.feature_transform_sha256,
        full_feature_order_sha256=manifest.full_feature_order_sha256,
        dependency_lock_sha256=manifest.dependency_lock_sha256,
        code_sha256=manifest.code_sha256,
    )


def _registry_report(
    root: Path, index: dict[str, Any], *, full_feature_count: int
) -> tuple[dict[str, Any], dict[str, AftPredictiveBundle]]:
    entries = index.get("entries")
    if not isinstance(entries, list) or len(entries) != 7:
        raise YearDatasetError("model registry index must contain seven final-comparison bundles")
    first_path = root / str(entries[0]["registry_path"]) / "manifest.json"
    first = ModelBundleManifest.from_payload(_load_json(first_path))
    registry = ModelRegistry(root / "registry", expectations=_expectations(first))
    bundles: dict[str, AftPredictiveBundle] = {}
    bundle_report: list[dict[str, Any]] = []
    calibrator_hashes: set[str] = set()
    for raw in entries:
        if not isinstance(raw, dict):
            raise YearDatasetError("registry entry must be an object")
        manifest_hash = str(raw.get("manifest_sha256", ""))
        manifest = registry.load_manifest(manifest_hash)
        if raw.get("bundle_id") != manifest.bundle_id:
            raise YearDatasetError("registry bundle identifier does not match its manifest")
        directory = root / str(raw.get("registry_path", ""))
        if directory != root / "registry" / manifest_hash:
            raise YearDatasetError("registry entry path is not hash-addressed")
        model_path = directory / "model.ubj"
        model_manifest_path = directory / "model-manifest.json"
        calibration_path = directory / "calibration.json"
        registry.validate(
            manifest,
            model_path=model_path,
            model_manifest_path=model_manifest_path,
            calibration_path=calibration_path,
            promoted=manifest_hash == index.get("promoted_manifest_sha256"),
        )
        calibration = _load_json(calibration_path)
        calibrator = calibration.get("calibrator")
        if (
            not isinstance(calibrator, dict)
            or calibrator.get("family") != "positive-slope-logistic-v1"
            or calibration.get("final_test_outcomes_opened") is not False
            or calibration.get("selection_freeze_sha256") != index.get("selection_freeze_sha256")
        ):
            raise YearDatasetError("registered calibrator violates the frozen protocol")
        calibrator_hashes.add(manifest.calibrator_sha256)
        bundle = AftPredictiveBundle.load(directory, full_feature_count=full_feature_count)
        bundles[manifest.bundle_id] = bundle
        bundle_report.append(
            {
                "bundle_id": manifest.bundle_id,
                "bundle_kind": manifest.bundle_kind,
                "calibrator_sha256": manifest.calibrator_sha256,
                "directory_bytes": sum(
                    path.stat().st_size for path in directory.iterdir() if path.is_file()
                ),
                "distribution": manifest.aft_distribution,
                "feature_count": len(manifest.model_feature_names),
                "manifest_sha256": manifest_hash,
            }
        )
    return (
        {
            "bundles": bundle_report,
            "calibrator_hash_count": len(calibrator_hashes),
            "distributions": sorted(
                {bundle.manifest.aft_distribution for bundle in bundles.values()}
            ),
            "kinds": sorted({bundle.manifest.bundle_kind for bundle in bundles.values()}),
        },
        bundles,
    )


def _validation_prefix(entry: dict[str, Any]) -> tuple[float, float, float, int]:
    validation = entry.get("validation")
    if not isinstance(validation, dict):
        raise YearDatasetError("validation comparison entry is incomplete")
    return (
        float(validation["weighted_interval_negative_log_likelihood"]),
        float(validation["weighted_horizon_brier_score"]),
        float(validation["worst_supported_horizon_calibration_error"]),
        int(entry["parameter_count"]),
    )


def _selection_report(root: Path, index: dict[str, Any], latency: dict[str, Any]) -> dict[str, Any]:
    comparison_path = _single(root, "validation-comparison-*.json")
    comparison = _load_json(comparison_path)
    if _digest(comparison_path) != index.get("validation_comparison_sha256"):
        raise YearDatasetError("validation comparison hash does not match the registry index")
    freeze_path = _single(root, "selection-freeze-*.json")
    freeze = _load_json(freeze_path)
    if _digest(freeze_path) != index.get("selection_freeze_sha256"):
        raise YearDatasetError("selection freeze hash does not match the registry index")
    if any(
        payload.get("final_test_outcomes_opened") is not False
        for payload in (comparison, freeze, index)
    ) or any(
        payload.get("calibration_fit_accessed") is not False for payload in (comparison, freeze)
    ):
        raise YearDatasetError("selection artifacts prove forbidden outcome access")
    grid = comparison.get("candidate_grid")
    baselines = comparison.get("aft_baselines")
    ablations = comparison.get("diagnostic_ablations")
    if not isinstance(grid, list) or len(grid) != 6:
        raise YearDatasetError("candidate grid must contain six frozen configurations")
    if not isinstance(baselines, list) or len(baselines) != 2:
        raise YearDatasetError("AFT baseline comparison is incomplete")
    if not isinstance(ablations, list) or len(ablations) != 2:
        raise YearDatasetError("ablation comparison is incomplete")
    selected = min(grid, key=_validation_prefix)
    strongest_baseline = min(baselines, key=_validation_prefix)
    if selected["identifier"] != comparison.get("selected_full_candidate_id"):
        raise YearDatasetError("selected full candidate violates the frozen ordering")
    if strongest_baseline["identifier"] != comparison.get("strongest_aft_baseline_id"):
        raise YearDatasetError("strongest AFT baseline violates the frozen ordering")
    promoted_expected = (
        selected
        if _validation_prefix(selected) < _validation_prefix(strongest_baseline)
        else strongest_baseline
    )
    if promoted_expected["identifier"] != comparison.get("promoted_bundle_id"):
        raise YearDatasetError("promoted model violates the frozen ordering")
    raw_latency_models = latency.get("models")
    if not isinstance(raw_latency_models, list):
        raise YearDatasetError("latency artifact model list is invalid")
    latency_models = {
        str(model["identifier"]): float(model["latency_seconds"]) for model in raw_latency_models
    }
    final_ids = comparison.get("final_compared_bundle_ids")
    if not isinstance(final_ids, list) or set(final_ids) != set(latency_models):
        raise YearDatasetError("latency artifact does not cover every final-compared bundle")
    if comparison.get("scoring_latency_resolution_seconds") != LATENCY_RESOLUTION_SECONDS:
        raise YearDatasetError("selection latency resolution is not frozen")
    return {
        "ablation_bundle_ids": [str(entry["identifier"]) for entry in ablations],
        "candidate_count": len(grid),
        "final_compared_bundle_ids": final_ids,
        "latency_seconds": latency_models,
        "promoted_bundle_id": comparison["promoted_bundle_id"],
        "selected_full_candidate": selected,
        "strongest_aft_baseline": strongest_baseline,
    }


def _likelihood_fixture_report() -> dict[str, float]:
    margins = np.full(4, math.log(100), dtype=np.float64)
    lower = np.asarray([100, 50, 0, 100], dtype=np.float64)
    upper = np.asarray([100, 150, 100, math.inf], dtype=np.float64)
    weights = np.ones(4, dtype=np.float64)
    return {
        distribution.value: weighted_interval_nll(
            margins,
            lower,
            upper,
            weights,
            scale=1.0,
            distribution=distribution,
        )
        for distribution in AftDistribution
    }


def _prediction_report(
    primary: AftPredictiveBundle,
    restart: AftPredictiveBundle,
    validation: Any,
) -> dict[str, Any]:
    primary_probabilities = primary.cdf_matrix(validation.features, HORIZONS)
    restart_probabilities = restart.cdf_matrix(validation.features, HORIZONS)
    maximum_delta = float(np.max(np.abs(primary_probabilities - restart_probabilities)))
    sample = validation.features[:256]
    predictions = primary.predict(sample)
    quantile_consistent = True
    quantile_ordered = True
    horizon_behavior = True
    for prediction in predictions:
        quantiles = (prediction.p50, prediction.p80, prediction.p90)
        resolved_upper = [
            quantile.upper_seconds for quantile in quantiles if quantile.upper_seconds is not None
        ]
        if resolved_upper != sorted(resolved_upper):
            quantile_ordered = False
        unresolved_seen = False
        for quantile in quantiles:
            if not quantile.resolved_within_horizon:
                unresolved_seen = True
                if quantile.lower_seconds is not None or quantile.upper_seconds is not None:
                    horizon_behavior = False
                continue
            if unresolved_seen or quantile.lower_seconds is None or quantile.upper_seconds is None:
                quantile_ordered = False
                continue
            if quantile.upper_seconds > 3600:
                horizon_behavior = False
            lower_probability = primary._calibrated_cdf(
                quantile.lower_seconds, prediction.raw_margin
            )
            upper_probability = primary._calibrated_cdf(
                quantile.upper_seconds, prediction.raw_margin
            )
            if not lower_probability < quantile.probability <= upper_probability:
                quantile_consistent = False
    raw_margins = primary.raw_margins(sample)
    default_event_time = np.exp(raw_margins)
    raw_margin_semantics = bool(
        np.any(default_event_time > 1)
        and not np.allclose(default_event_time[:, None], primary_probabilities[:256])
    )
    return {
        "all_probabilities_finite": bool(np.all(np.isfinite(primary_probabilities))),
        "horizon_monotonic": bool(
            np.all(primary_probabilities[:, 1:] >= primary_probabilities[:, :-1])
        ),
        "maximum_absolute_restart_delta": maximum_delta,
        "probabilities_bounded": bool(
            np.all((primary_probabilities >= 0) & (primary_probabilities <= 1))
        ),
        "probability_count": int(primary_probabilities.size),
        "quantile_cdf_consistent": quantile_consistent,
        "quantiles_ordered": quantile_ordered,
        "raw_event_time_rejected_as_probability": raw_margin_semantics,
        "row_count": int(primary_probabilities.shape[0]),
        "sixty_minute_horizon_behavior": horizon_behavior,
    }


def _tamper_report(
    registry: ModelRegistry,
    manifest: ModelBundleManifest,
    directory: Path,
) -> dict[str, bool]:
    model_path = directory / "model.ubj"
    model_manifest_path = directory / "model-manifest.json"
    calibration_path = directory / "calibration.json"
    mutations = {
        "CHANGED_SOURCE": replace(manifest, source_lock_sha256="0" * 64),
        "CHANGED_SPLIT": replace(manifest, split_manifest_sha256="0" * 64),
        "CHANGED_FEATURE_ORDER": replace(manifest, full_feature_order_sha256="0" * 64),
        "CHANGED_DEPENDENCY_LOCK": replace(manifest, dependency_lock_sha256="0" * 64),
    }
    results: dict[str, bool] = {}
    for name, changed in mutations.items():
        try:
            registry.validate(
                changed,
                model_path=model_path,
                model_manifest_path=model_manifest_path,
                calibration_path=calibration_path,
            )
        except ValueError:
            results[name] = True
        else:
            results[name] = False
    with tempfile.TemporaryDirectory(prefix="arrive90-m3-tamper-") as temporary_name:
        temporary = Path(temporary_name)
        changed_model = temporary / "model.ubj"
        changed_model.write_bytes(model_path.read_bytes() + b"changed")
        changed_calibration = temporary / "calibration.json"
        changed_calibration.write_bytes(calibration_path.read_bytes() + b"changed")
        for name, candidate_model, candidate_calibration in (
            ("CHANGED_MODEL", changed_model, calibration_path),
            ("CHANGED_CALIBRATOR", model_path, changed_calibration),
        ):
            try:
                registry.validate(
                    manifest,
                    model_path=candidate_model,
                    model_manifest_path=model_manifest_path,
                    calibration_path=candidate_calibration,
                )
            except ValueError:
                results[name] = True
            else:
                results[name] = False
    return results


def _point_baseline_report(path: Path, point: dict[str, Any]) -> dict[str, Any]:
    empirical = point["empirical_midpoint"]
    manifest = empirical["manifest"]
    return {
        "artifact_sha256": _digest(path),
        "empirical_midpoint": {
            "backoff_order": manifest["backoff_order"],
            "coverage_finite_row_count": empirical["coverage_finite_row_count"],
            "coverage_finite_weight": empirical["coverage_finite_weight"],
            "coverage_loss_weight": empirical["coverage_loss_weight"],
            "manifest_sha256": empirical["manifest_sha256"],
            "minimum_distinct_anchors": manifest["minimum_distinct_anchors"],
            "minimum_finite_examples": manifest["minimum_finite_examples"],
            "supported_cell_count": len(manifest["cells"]),
            "weighted_mean_interval_distance_seconds": empirical[
                "weighted_mean_interval_distance_seconds"
            ],
        },
        "fitting_split": point["fitting_split"],
        "official_schedule": point["official_schedule"],
        "validation_split": point["validation_split"],
        "version": point["version"],
    }


def build_reports() -> tuple[dict[str, Any], dict[str, Any]]:
    primary_index_path, primary_index, primary_index_sha256 = _index(PRIMARY_ROOT)
    restart_index_path, restart_index, restart_index_sha256 = _index(RESTART_ROOT)
    primary_runtime = _load_json(PRIMARY_RUNTIME / "training-run.json")
    restart_runtime = _load_json(RESTART_RUNTIME / "training-run.json")
    latency = _load_json(PRIMARY_RUNTIME / "latency.json")
    context = load_modeling_context(ROOT / "data/datasets/travel-time-v1")
    training = load_modeling_split(context, DatasetSplit.TRAINING)
    validation = load_modeling_split(context, DatasetSplit.MODEL_VALIDATION)
    calibration = load_modeling_split(context, DatasetSplit.CALIBRATION)
    final_sealed = False
    try:
        load_modeling_split(context, DatasetSplit.FINAL_TEST)
    except FinalTestOutcomeAccessError:
        final_sealed = True
    primary_registry_report, primary_bundles = _registry_report(
        PRIMARY_ROOT, primary_index, full_feature_count=validation.features.shape[1]
    )
    restart_registry_report, restart_bundles = _registry_report(
        RESTART_ROOT, restart_index, full_feature_count=validation.features.shape[1]
    )
    selection = _selection_report(PRIMARY_ROOT, primary_index, latency)
    promoted_id = str(primary_index["promoted_bundle_id"])
    predictions = _prediction_report(
        primary_bundles[promoted_id], restart_bundles[promoted_id], validation
    )
    promoted_manifest = primary_bundles[promoted_id].manifest
    promoted_directory = PRIMARY_ROOT / "registry" / promoted_manifest.manifest_hash
    registry = ModelRegistry(
        PRIMARY_ROOT / "registry",
        expectations=_expectations(promoted_manifest),
        promoted_bundle_size_bytes_max=PROMOTED_SIZE_LIMIT,
    )
    tamper = _tamper_report(registry, promoted_manifest, promoted_directory)
    point_path = _single(PRIMARY_ROOT, "point-baselines-*.json")
    point = _load_json(point_path)
    point_report = _point_baseline_report(point_path, point)
    make = shutil.which("make")
    if make is None:
        raise RuntimeError("make is required for Milestone 3 qualification")
    check = subprocess.run(  # noqa: S603 - fixed local executable and arguments.
        [make, "check"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    promoted_size = sum(
        path.stat().st_size for path in promoted_directory.iterdir() if path.is_file()
    )
    roots_identical = _tree_hashes(PRIMARY_ROOT) == _tree_hashes(RESTART_ROOT)
    expected_kinds = {
        "FULL",
        "INTERCEPT_ONLY",
        "SCHEDULE_CALENDAR",
        "NO_PREFIX_HISTORY",
        "NO_POSITION_OBSERVATION",
    }
    checks = {
        "all_final_compared_bundles_are_calibrated_serialized_and_hashed": (
            len(primary_registry_report["bundles"]) == 7
            and primary_registry_report["calibrator_hash_count"] == 7
        ),
        "candidate_grid_and_required_baselines_are_complete": (
            selection["candidate_count"] == 6
            and len(point["empirical_midpoint"]["manifest"]["cells"]) > 0
            and point["fitting_split"] == DatasetSplit.TRAINING.value
            and point["validation_split"] == DatasetSplit.MODEL_VALIDATION.value
        ),
        "cdf_probability_quantile_and_horizon_invariants_pass": all(
            predictions[name]
            for name in (
                "all_probabilities_finite",
                "horizon_monotonic",
                "probabilities_bounded",
                "quantile_cdf_consistent",
                "quantiles_ordered",
                "sixty_minute_horizon_behavior",
            )
        ),
        "deterministic_training_reproduces_predictions_and_manifest": (
            primary_index_sha256 == restart_index_sha256
            and primary_index_path.read_bytes() == restart_index_path.read_bytes()
            and roots_identical
            and predictions["maximum_absolute_restart_delta"] <= 1e-6
        ),
        "final_test_outcomes_remain_sealed": (
            final_sealed
            and primary_index.get("final_test_outcomes_opened") is False
            and restart_index.get("final_test_outcomes_opened") is False
        ),
        "frozen_model_selection_order_promotes_a_common_bundle": (
            selection["promoted_bundle_id"] == promoted_id
            and primary_bundles[promoted_id].metadata.manifest_hash
            == primary_index["promoted_manifest_sha256"]
        ),
        "interval_exact_left_and_right_likelihood_fixtures_pass": all(
            math.isfinite(value) and value > 0 for value in _likelihood_fixture_report().values()
        ),
        "make_check_passed": check.returncode == 0,
        "normal_logistic_and_extreme_value_distributions_are_registered": (
            primary_registry_report["distributions"] == ["extreme", "logistic", "normal"]
        ),
        "predeclared_ablations_are_trained_and_frozen": (
            set(selection["ablation_bundle_ids"])
            == {"NO_PREFIX_HISTORY-normal", "NO_POSITION_OBSERVATION-normal"}
            and expected_kinds.issubset(primary_registry_report["kinds"])
        ),
        "promoted_bundle_is_within_committed_demo_budget": promoted_size <= PROMOTED_SIZE_LIMIT,
        "raw_margin_semantics_are_not_default_event_time_semantics": predictions[
            "raw_event_time_rejected_as_probability"
        ],
        "registry_rejects_every_seeded_lineage_and_byte_tamper": all(tamper.values()),
        "selection_and_calibration_never_read_final_test": (
            primary_runtime.get("final_test_outcomes_opened") is False
            and restart_runtime.get("final_test_outcomes_opened") is False
        ),
        "source_dataset_split_feature_dependency_and_code_hashes_are_bound": (
            promoted_manifest.source_lock_sha256 == context.source_lock_sha256
            and promoted_manifest.dataset_manifest_sha256 == context.population_manifest_sha256
            and promoted_manifest.split_manifest_sha256 == context.split_manifest_sha256
            and promoted_manifest.feature_registry_sha256 == TRAVEL_TIME_V1_REGISTRY.manifest_hash
            and promoted_manifest.feature_transform_sha256 == context.feature_transform_sha256
            and promoted_manifest.dependency_lock_sha256 == _digest(ROOT / "uv.lock")
        ),
    }
    failing = sorted(name for name, passed in checks.items() if not passed)
    observed = {
        "calibration_row_count": calibration.features.shape[0],
        "likelihood_fixtures": _likelihood_fixture_report(),
        "point_baselines": point_report,
        "prediction_reproducibility": predictions,
        "primary_registry": primary_registry_report,
        "promoted_bundle_bytes": promoted_size,
        "registry_restart": restart_registry_report,
        "selection": selection,
        "tamper_probes": tamper,
        "training_row_count": training.features.shape[0],
        "validation_row_count": validation.features.shape[0],
    }
    input_hashes = {
        "acceptance_charter": _digest(ROOT / "configs/acceptance/travel-time-v1.2.yaml"),
        "build_plan": _digest(ROOT / "BUILD_PLAN.md"),
        "model_config": _digest(ROOT / "configs/models/travel-time-v1.json"),
        "model_population_manifest": context.population_manifest_sha256,
        "primary_registry_index": primary_index_sha256,
        "restart_registry_index": restart_index_sha256,
        "uv_lock": _digest(ROOT / "uv.lock"),
    }
    environment = {
        "implementation_commit": subprocess.run(  # noqa: S603 - resolved trusted git.
            [GIT, "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip(),
        "machine": platform.machine(),
        "platform": platform.platform(),
        "python": platform.python_version(),
    }
    qualification: dict[str, Any] = {
        "acceptance_version": DEFAULT_ACCEPTANCE_VERSION,
        "checks": checks,
        "environment": environment,
        "failing_checks": failing,
        "input_manifest_hashes": input_hashes,
        "make_check_output_tail": (check.stdout + check.stderr)[-4_000:],
        "observed": observed,
        "qualification_command": "make qualify-milestone3",
        "state": "PASSED" if not failing else "FAILED",
    }
    QUALIFICATION_PATH.parent.mkdir(parents=True, exist_ok=True)
    QUALIFICATION_PATH.write_text(
        json.dumps(qualification, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    gate: dict[str, Any] = {
        "acceptance_charter_sha256": input_hashes["acceptance_charter"],
        "acceptance_version": DEFAULT_ACCEPTANCE_VERSION,
        "checks": checks,
        "command": "make qualify-milestone3 && make gate MILESTONE=3",
        "environment": environment,
        "failing_checks": failing,
        "input_manifest_hashes": input_hashes,
        "milestone": 3,
        "observed": observed,
        "qualification_report_sha256": _digest(QUALIFICATION_PATH),
        "state": "ACCEPTED" if not failing else "FAILED",
    }
    GATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    GATE_PATH.write_text(json.dumps(gate, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return qualification, gate


def main() -> int:
    qualification, _gate = build_reports()
    print(QUALIFICATION_PATH.relative_to(ROOT))
    print(GATE_PATH.relative_to(ROOT))
    return 0 if qualification["state"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
