"""Frozen model, prediction, replay, and demo artifacts for final evaluation."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, cast

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from arrive90_data_contracts.gates import DEFAULT_ACCEPTANCE_VERSION
from arrive90_models.predictive_bundle import AftPredictiveBundle
from arrive90_outcomes.travel_time_baselines import EmpiricalMidpointBaseline

from arrive90_evaluation.final_data import FinalEvaluationData, FinalFeatureInventory
from arrive90_evaluation.final_metrics import FinalModelPredictions
from arrive90_evaluation.year_dataset import YearDatasetError


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def value_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def write_content_addressed_json(root: Path, stem: str, payload: object) -> tuple[Path, str]:
    body = canonical_json(payload)
    digest = hashlib.sha256(body).hexdigest()
    path = root / f"{stem}-{digest}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != body:
        raise YearDatasetError(f"content-addressed artifact has conflicting bytes: {path}")
    if not path.exists():
        path.write_bytes(body)
    return path, digest


def write_pretty_json(path: Path, payload: object) -> str:
    body = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False).encode() + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != body:
        raise YearDatasetError(f"committed artifact has conflicting bytes: {path}")
    path.write_bytes(body)
    return hashlib.sha256(body).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise YearDatasetError(f"{path} must contain a JSON object")
    return payload


@dataclass(frozen=True, slots=True)
class LoadedModelRegistry:
    root: Path
    index_path: Path
    index_sha256: str
    index: dict[str, Any]
    bundles: dict[str, AftPredictiveBundle]
    promoted_bundle_id: str

    @property
    def promoted(self) -> AftPredictiveBundle:
        return self.bundles[self.promoted_bundle_id]


def load_model_registry(
    model_root: Path,
    *,
    full_feature_count: int,
) -> LoadedModelRegistry:
    paths = sorted(model_root.glob("registry-index-*.json"))
    if len(paths) != 1:
        raise YearDatasetError("final evaluation requires exactly one model registry index")
    index_path = paths[0]
    index_sha256 = file_sha256(index_path)
    if index_path.stem != f"registry-index-{index_sha256}":
        raise YearDatasetError("model registry index filename is not content addressed")
    index = _load_json(index_path)
    if (
        index.get("acceptance_version") != DEFAULT_ACCEPTANCE_VERSION
        or index.get("final_test_outcomes_opened") is not False
    ):
        raise YearDatasetError("model registry was not frozen before final-test access")
    entries = index.get("entries")
    if not isinstance(entries, list) or len(entries) != 7:
        raise YearDatasetError("final model registry must contain seven compared bundles")
    bundles: dict[str, AftPredictiveBundle] = {}
    for raw in entries:
        if not isinstance(raw, dict):
            raise YearDatasetError("model registry entry must be an object")
        bundle_id = str(raw["bundle_id"])
        manifest_sha256 = str(raw["manifest_sha256"])
        directory = model_root / str(raw["registry_path"])
        if directory != model_root / "registry" / manifest_sha256:
            raise YearDatasetError("model registry entry is not hash addressed")
        bundle = AftPredictiveBundle.load(directory, full_feature_count=full_feature_count)
        if (
            bundle.manifest.bundle_id != bundle_id
            or bundle.manifest.manifest_hash != manifest_sha256
            or bundle.manifest.final_test_outcomes_opened
        ):
            raise YearDatasetError("model registry manifest identity is invalid")
        bundles[bundle_id] = bundle
    promoted = str(index["promoted_bundle_id"])
    if (
        promoted not in bundles
        or index.get("promoted_manifest_sha256") != bundles[promoted].manifest.manifest_hash
    ):
        raise YearDatasetError("promoted final model does not match the frozen registry")
    return LoadedModelRegistry(
        model_root,
        index_path,
        index_sha256,
        index,
        bundles,
        promoted,
    )


def load_empirical_baseline(model_root: Path) -> tuple[EmpiricalMidpointBaseline, str]:
    paths = sorted(model_root.glob("point-baselines-*.json"))
    if len(paths) != 1:
        raise YearDatasetError("final evaluation requires one point-baseline artifact")
    payload = _load_json(paths[0])
    empirical = payload.get("empirical_midpoint")
    if not isinstance(empirical, dict) or not isinstance(empirical.get("manifest"), dict):
        raise YearDatasetError("point-baseline artifact is incomplete")
    baseline = EmpiricalMidpointBaseline.from_manifest(empirical["manifest"])
    if baseline.manifest_sha256 != empirical.get("manifest_sha256"):
        raise YearDatasetError("empirical baseline manifest hash is invalid")
    return baseline, file_sha256(paths[0])


def _station_coordinate_rows(
    schedule_database: Path,
) -> dict[str, list[tuple[int, int, float, float]]]:
    if not schedule_database.is_file():
        raise YearDatasetError("official schedule database is missing")
    result: dict[str, list[tuple[int, int, float, float]]] = {}
    uri = f"file:{schedule_database.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        rows = connection.execute(
            "SELECT stop_id, gtfs_active_date, gtfs_end_date, stop_lat, stop_lon "
            "FROM stops WHERE stop_lat IS NOT NULL AND stop_lon IS NOT NULL"
        ).fetchall()
    for stop_id, active, end, latitude, longitude in rows:
        result.setdefault(str(stop_id), []).append(
            (int(active), int(end), float(latitude), float(longitude))
        )
    return result


def _active_station_coordinate(
    rows: dict[str, list[tuple[int, int, float, float]]],
    stop_id: str,
    service_date: date,
) -> tuple[float, float] | None:
    encoded = int(service_date.strftime("%Y%m%d"))
    matches = {
        (latitude, longitude)
        for active, end, latitude, longitude in rows.get(stop_id, [])
        if active <= encoded <= end
    }
    if len(matches) != 1:
        return None
    return next(iter(matches))


@dataclass(frozen=True, slots=True)
class ReplaySelection:
    indices: tuple[int, ...]
    manifest: dict[str, Any]
    manifest_sha256: str
    station_coordinates: dict[str, Any]


def build_replay_selection(
    inventory: FinalFeatureInventory,
    promoted: AftPredictiveBundle,
    *,
    schedule_database: Path,
    replay_config: dict[str, Any],
) -> ReplaySelection:
    """Select outcome-blind examples whose public-station coordinate replay is exact."""

    maximum = int(replay_config["maximum_examples"])
    per_month = int(replay_config["maximum_examples_per_month"])
    if maximum != 200 or per_month != 100:
        raise YearDatasetError("final replay limits must remain 200 total and 100 per month")
    coordinates = _station_coordinate_rows(schedule_database)
    names = inventory.context.feature_transform.column_names
    latitude_index = names.index("anchor_latitude")
    longitude_index = names.index("anchor_longitude")
    substitute = inventory.features.tolil(copy=True)
    available = np.zeros(len(inventory.rows), dtype=np.bool_)
    station_keys: list[str | None] = []
    selected_coordinate_rows: dict[str, dict[str, Any]] = {}
    for row_index, row in enumerate(inventory.rows):
        coordinate = _active_station_coordinate(
            coordinates, row.query.origin_stop_id, row.service_date
        )
        if coordinate is None:
            station_keys.append(None)
            continue
        latitude, longitude = coordinate
        substitute[row_index, latitude_index] = latitude
        substitute[row_index, longitude_index] = longitude
        available[row_index] = True
        station_key = f"{row.query.origin_stop_id}|{row.service_date.isoformat()}"
        station_keys.append(station_key)
        selected_coordinate_rows[station_key] = {
            "latitude": latitude,
            "longitude": longitude,
            "origin_stop_id": row.query.origin_stop_id,
            "service_date": row.service_date.isoformat(),
            "source": "official MBTA GTFS schedule archive",
        }
    substitute_csr = substitute.tocsr()
    substitute_csr.sort_indices()
    actual_margins = promoted.raw_margins(inventory.features)
    substitute_margins = promoted.raw_margins(substitute_csr)
    invariant = available & np.isclose(actual_margins, substitute_margins, rtol=0.0, atol=1e-12)
    seed = str(replay_config["selection_seed"]).encode()
    candidates: dict[str, list[tuple[bytes, int]]] = {}
    for row_index, row in enumerate(inventory.rows):
        if not invariant[row_index]:
            continue
        month = row.service_date.strftime("%Y-%m")
        digest = hmac.new(seed, row.source_example_sha256.encode(), hashlib.sha256).digest()
        candidates.setdefault(month, []).append((digest, row_index))
    chosen: list[tuple[bytes, int]] = []
    for month in ("2024-11", "2024-12"):
        month_rows = sorted(candidates.get(month, []), key=lambda item: item[0])
        if len(month_rows) < per_month:
            raise YearDatasetError(
                f"outcome-blind replay selection has only {len(month_rows)} exact {month} rows"
            )
        chosen.extend(month_rows[:per_month])
    chosen.sort(key=lambda item: item[0])
    indices = tuple(row_index for _, row_index in chosen[:maximum])
    entries: list[dict[str, Any]] = []
    used_station_keys: set[str] = set()
    for digest, row_index in chosen[:maximum]:
        row = inventory.rows[row_index]
        selected_station_key: str | None = station_keys[row_index]
        if selected_station_key is None:
            raise YearDatasetError("selected replay row has no public station coordinate")
        used_station_keys.add(selected_station_key)
        entries.append(
            {
                "hmac_sha256": digest.hex(),
                "month": row.service_date.strftime("%Y-%m"),
                "replay_id": digest.hex()[:24],
                "service_date": row.service_date.isoformat(),
                "source_example_sha256": row.source_example_sha256,
                "station_coordinate_key": selected_station_key,
            }
        )
    manifest = {
        "acceptance_version": DEFAULT_ACCEPTANCE_VERSION,
        "candidate_rule": (
            "final feature row whose promoted raw margin is unchanged after replacing raw "
            "vehicle coordinates with the active public GTFS origin-stop coordinate"
        ),
        "entries": entries,
        "final_test_outcomes_opened": False,
        "invariant_candidate_count_by_month": {
            month: len(values) for month, values in sorted(candidates.items())
        },
        "maximum_examples": maximum,
        "model_manifest_sha256": promoted.manifest.manifest_hash,
        "position_substitution": replay_config["position_substitution"],
        "selection_hash": replay_config["selection_hash"],
        "selection_inputs": [
            "source_example_sha256",
            "service_date",
            "month",
            "feature-derived scoring invariance",
        ],
        "selection_seed": replay_config["selection_seed"],
        "split": "FINAL_TEST",
        "version": "travel-time-replay-selection-v1",
    }
    manifest_sha256 = value_sha256(manifest)
    station_payload = {
        "acceptance_version": DEFAULT_ACCEPTANCE_VERSION,
        "coordinates": {
            key: selected_coordinate_rows[key] for key in sorted(used_station_keys, key=str.encode)
        },
        "version": "travel-time-public-station-coordinates-v1",
    }
    return ReplaySelection(indices, manifest, manifest_sha256, station_payload)


def evaluation_code_sha256(paths: tuple[Path, ...], *, root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.as_posix().encode()):
        if not path.is_file():
            raise YearDatasetError(f"frozen evaluation source is missing: {path}")
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class PredictionArtifact:
    path: Path
    sha256: str
    manifest_path: Path
    manifest_sha256: str
    manifest: dict[str, Any]


def write_prediction_artifact(
    runtime_root: Path,
    data: FinalEvaluationData,
    predictions: dict[str, FinalModelPredictions],
    empirical: EmpiricalMidpointBaseline,
    *,
    protocol_sha256: str,
    replay_selection_sha256: str,
    model_order: tuple[str, ...],
) -> PredictionArtifact:
    """Write the immutable, identifier-hashed final prediction table."""

    if tuple(predictions) != model_order:
        raise YearDatasetError("final prediction model order is not frozen")
    rows = data.inventory.rows
    columns: dict[str, Any] = {
        "source_example_sha256": [row.source_example_sha256 for row in rows],
        "anchor_sha256": [hashlib.sha256(row.anchor_id.encode()).hexdigest() for row in rows],
        "service_date": [row.service_date for row in rows],
        "analysis_weight": data.analysis_weights,
        "outcome_state": list(data.outcome_states),
        "lower_bound_seconds": [
            None if math.isnan(value) else float(value) for value in data.lower_bounds
        ],
        "upper_bound_seconds": [
            None if math.isnan(value) else float(value) for value in data.upper_bounds
        ],
    }
    columns["official_schedule_seconds"] = [
        float(cast(int | float, row.values["scheduled_remaining_seconds"])) for row in rows
    ]
    empirical_predictions = [empirical.predict(row.query) for row in rows]
    columns["empirical_midpoint_seconds"] = [value.seconds for value in empirical_predictions]
    columns["empirical_backoff_level"] = [value.backoff_level for value in empirical_predictions]
    slice_names = tuple(name for name, _ in rows[0].slices)
    for slice_name in slice_names:
        columns[f"slice__{slice_name}"] = [row.slice_values[slice_name] for row in rows]
    model_columns: dict[str, Any] = {}
    horizons = (300, 600, 900, 1200, 1800, 2700, 3600)
    quantiles = (50, 80, 90)
    for model_index, bundle_id in enumerate(model_order):
        prefix = f"model_{model_index:02d}"
        prediction = predictions[bundle_id]
        columns[f"{prefix}__raw_margin"] = prediction.raw_margins
        for horizon_index, horizon in enumerate(horizons):
            columns[f"{prefix}__p_{horizon}"] = prediction.probabilities[:, horizon_index]
        for quantile_index, quantile in enumerate(quantiles):
            columns[f"{prefix}__q_{quantile}"] = [
                None if math.isnan(value) else float(value)
                for value in prediction.quantiles_seconds[:, quantile_index]
            ]
            columns[f"{prefix}__q_{quantile}_resolved"] = prediction.quantiles_resolved[
                :, quantile_index
            ]
        model_columns[bundle_id] = {
            "distribution": prediction.distribution,
            "manifest_sha256": prediction.manifest_sha256,
            "prefix": prefix,
            "scale": prediction.scale,
        }
    table = pa.table(columns)
    runtime_root.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".final-predictions-", suffix=".parquet", dir=runtime_root
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        pq.write_table(
            table,
            temporary,
            compression="zstd",
            compression_level=9,
            data_page_version="2.0",
            use_dictionary=True,
            version="2.6",
            write_statistics=True,
        )
        prediction_sha256 = file_sha256(temporary)
        path = runtime_root / f"final-predictions-{prediction_sha256}.parquet"
        if path.exists():
            if file_sha256(path) != prediction_sha256:
                raise YearDatasetError("prediction artifact destination has conflicting bytes")
        else:
            os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    manifest = {
        "acceptance_version": DEFAULT_ACCEPTANCE_VERSION,
        "column_names": table.column_names,
        "empirical_baseline_manifest_sha256": empirical.manifest_sha256,
        "final_test_outcomes_opened": True,
        "model_columns": model_columns,
        "model_order": list(model_order),
        "outcome_manifest_sha256": data.outcome_manifest_sha256,
        "prediction_file": path.name,
        "prediction_sha256": prediction_sha256,
        "protocol_sha256": protocol_sha256,
        "replay_selection_sha256": replay_selection_sha256,
        "row_count": table.num_rows,
        "row_manifest_sha256": data.inventory.row_manifest_sha256,
        "slice_names": list(slice_names),
        "split": "FINAL_TEST",
        "version": "travel-time-final-predictions-v1",
    }
    manifest_path, manifest_sha256 = write_content_addressed_json(
        runtime_root, "final-prediction-manifest", manifest
    )
    return PredictionArtifact(path, prediction_sha256, manifest_path, manifest_sha256, manifest)


def load_prediction_artifact(manifest_path: Path) -> tuple[dict[str, Any], pa.Table]:
    manifest = _load_json(manifest_path)
    path = manifest_path.parent / str(manifest.get("prediction_file", ""))
    if (
        manifest.get("acceptance_version") != DEFAULT_ACCEPTANCE_VERSION
        or not path.is_file()
        or file_sha256(path) != manifest.get("prediction_sha256")
    ):
        raise YearDatasetError("frozen prediction artifact failed verification")
    table = pq.read_table(path)
    if table.num_rows != manifest.get("row_count") or table.column_names != manifest.get(
        "column_names"
    ):
        raise YearDatasetError("frozen prediction table does not match its manifest")
    return manifest, table


def copy_promoted_bundle(
    registry: LoadedModelRegistry,
    destination_root: Path,
    *,
    size_limit_bytes: int,
) -> tuple[Path, str, int]:
    manifest_sha256 = registry.promoted.manifest.manifest_hash
    source = registry.root / "registry" / manifest_sha256
    destination = destination_root / "model" / manifest_sha256
    files = ("calibration.json", "manifest.json", "model-manifest.json", "model.ubj")
    size = sum((source / name).stat().st_size for name in files)
    if size > size_limit_bytes:
        raise YearDatasetError("promoted demo bundle exceeds its frozen size limit")
    destination.mkdir(parents=True, exist_ok=True)
    for name in files:
        source_path = source / name
        destination_path = destination / name
        if destination_path.exists() and destination_path.read_bytes() != source_path.read_bytes():
            raise YearDatasetError("committed demo bundle conflicts with evaluated bytes")
        shutil.copyfile(source_path, destination_path)
    copied_hashes = {name: file_sha256(destination / name) for name in files}
    source_hashes = {name: file_sha256(source / name) for name in files}
    if copied_hashes != source_hashes or copied_hashes["manifest.json"] != manifest_sha256:
        raise YearDatasetError("committed demo bundle is not byte-identical to evaluation")
    return destination, value_sha256(copied_hashes), size


def _contains_forbidden_key(value: object, forbidden: set[str]) -> bool:
    if isinstance(value, dict):
        return any(
            str(key).lower() in forbidden or _contains_forbidden_key(item, forbidden)
            for key, item in value.items()
        )
    if isinstance(value, list | tuple):
        return any(_contains_forbidden_key(item, forbidden) for item in value)
    return False


def write_replay_artifacts(
    demo_root: Path,
    selection: ReplaySelection,
    data: FinalEvaluationData,
    promoted: FinalModelPredictions,
    *,
    forbidden_fields: tuple[str, ...],
) -> dict[str, Any]:
    """Write the redacted outcome-blind selection manifest and reveal fixture."""

    selection_path = demo_root / "replay-selection.json"
    station_path = demo_root / "station-coordinates.json"
    selection_file_sha256 = write_pretty_json(selection_path, selection.manifest)
    station_sha256 = write_pretty_json(station_path, selection.station_coordinates)
    entries_by_source = {
        str(entry["source_example_sha256"]): entry for entry in selection.manifest["entries"]
    }
    replay_rows: list[dict[str, Any]] = []
    for row_index in selection.indices:
        row = data.inventory.rows[row_index]
        selection_entry = entries_by_source[row.source_example_sha256]
        safe_features = {
            name: value
            for name, value in row.feature_values
            if name not in {"anchor_latitude", "anchor_longitude"}
        }
        replay_rows.append(
            {
                "feature_payload": {
                    "feature_schema_sha256": data.inventory.context.feature_transform_sha256,
                    "registered_values": safe_features,
                    "station_coordinate_key": selection_entry["station_coordinate_key"],
                },
                "offline_prediction": {
                    "horizons_seconds": [300, 600, 900, 1200, 1800, 2700, 3600],
                    "probabilities": promoted.probabilities[row_index].tolist(),
                    "quantiles_seconds": [
                        None if math.isnan(value) else float(value)
                        for value in promoted.quantiles_seconds[row_index]
                    ],
                    "quantiles_resolved": promoted.quantiles_resolved[row_index].tolist(),
                    "raw_margin": float(promoted.raw_margins[row_index]),
                },
                "outcome_reveal": {
                    "lower_bound_seconds": (
                        None
                        if math.isnan(data.lower_bounds[row_index])
                        else float(data.lower_bounds[row_index])
                    ),
                    "outcome_state": data.outcome_states[row_index],
                    "upper_bound_seconds": (
                        None
                        if math.isnan(data.upper_bounds[row_index])
                        else float(data.upper_bounds[row_index])
                    ),
                },
                "replay_id": selection_entry["replay_id"],
                "service_date": row.service_date.isoformat(),
                "slices": row.slice_values,
                "source_example_sha256": row.source_example_sha256,
            }
        )
    fixture = {
        "acceptance_version": DEFAULT_ACCEPTANCE_VERSION,
        "feature_payload_excludes_outcomes": True,
        "model_manifest_sha256": promoted.manifest_sha256,
        "replay_count": len(replay_rows),
        "replay_selection_manifest_sha256": selection.manifest_sha256,
        "replays": replay_rows,
        "split": "FINAL_TEST",
        "station_coordinate_artifact_sha256": station_sha256,
        "version": "travel-time-held-out-replay-v1",
    }
    forbidden = {value.lower() for value in forbidden_fields}
    if len(replay_rows) > 200 or _contains_forbidden_key(fixture, forbidden):
        raise YearDatasetError("held-out replay fixture violates its redaction contract")
    fixture_path = demo_root / "replay-fixture.json"
    fixture_sha256 = write_pretty_json(fixture_path, fixture)
    return {
        "fixture_path": fixture_path.as_posix(),
        "fixture_sha256": fixture_sha256,
        "replay_count": len(replay_rows),
        "selection_file_sha256": selection_file_sha256,
        "selection_manifest_sha256": selection.manifest_sha256,
        "selection_path": selection_path.as_posix(),
        "station_coordinates_path": station_path.as_posix(),
        "station_coordinates_sha256": station_sha256,
    }
