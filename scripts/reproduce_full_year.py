"""Rebuild or verify the complete 2024 pipeline from the immutable acquired-content lock."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = ROOT / "artifacts/reproduction/full-year-terminal.json"
DEFAULT_OUTPUT = ROOT / "artifacts/runtime/full-year-terminal.json"
BUFFER_BYTES = 4 * 1024 * 1024


class ReproductionError(ValueError):
    """The immutable full-year reproduction contract did not verify."""


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(BUFFER_BYTES):
            digest.update(block)
    return digest.hexdigest()


def _load(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReproductionError(f"reproduction manifest unavailable: {path}") from error
    if not isinstance(payload, dict):
        raise ReproductionError(f"reproduction manifest must be an object: {path}")
    return payload


def _gate_hash(name: str, key: str) -> str:
    gate = _load(ROOT / f"artifacts/reports/gates/{name}.json")
    value = gate.get("input_manifest_hashes", {}).get(key)
    if not isinstance(value, str) or len(value) != 64:
        raise ReproductionError(f"{name} does not bind {key}")
    return value


def _single_hash_path(root: Path, pattern: str, expected_hash: str) -> Path:
    matches = sorted(root.glob(pattern))
    matching = [path for path in matches if _digest(path) == expected_hash]
    if len(matching) != 1:
        raise ReproductionError(f"expected one {pattern} artifact with hash {expected_hash}")
    return matching[0]


def _verify_file(path: Path, *, size: int | None, sha256: str, label: str) -> int:
    if not path.is_file():
        raise ReproductionError(f"{label} is missing: {path}")
    observed_size = path.stat().st_size
    if size is not None and observed_size != size:
        raise ReproductionError(f"{label} size changed: {path}")
    if _digest(path) != sha256:
        raise ReproductionError(f"{label} bytes changed: {path}")
    return observed_size


def _raw_path(data_root: Path, source_key: str) -> Path:
    if source_key.endswith(".parquet"):
        return data_root / "raw/bus-observatory/mbta_all" / Path(source_key).name
    if source_key.endswith("GTFS_ARCHIVE.db.gz"):
        return data_root / "raw/mbta-gtfs/2024/GTFS_ARCHIVE.db.gz"
    raise ReproductionError(f"unknown acquired-content key: {source_key}")


def verify_acquisition(data_root: Path) -> dict[str, Any]:
    lock_path = ROOT / "configs/source-locks/mbta-2024-acquired.json"
    lock = _load(lock_path)
    entries = lock.get("content_entries")
    derived = lock.get("derived_entries")
    if not isinstance(entries, list) or len(entries) != 369 or not isinstance(derived, list):
        raise ReproductionError("full-year acquisition lock has an unexpected inventory")
    total_bytes = 0
    vehicle_rows = 0
    vehicle_objects = 0
    for index, raw in enumerate(entries):
        if not isinstance(raw, dict):
            raise ReproductionError(f"acquisition entry {index} is invalid")
        key = str(raw["source_object_key"])
        total_bytes += _verify_file(
            _raw_path(data_root, key),
            size=int(raw["response_size_bytes"]),
            sha256=str(raw["sha256"]),
            label="acquired object",
        )
        if key.endswith(".parquet"):
            vehicle_objects += 1
            vehicle_rows += int(raw["row_count"])
    if len(derived) != 1 or not isinstance(derived[0], dict):
        raise ReproductionError("schedule derivative lock is invalid")
    schedule = derived[0]
    total_bytes += _verify_file(
        data_root / "raw/mbta-gtfs/2024/GTFS_ARCHIVE.db",
        size=int(schedule["output_size_bytes"]),
        sha256=str(schedule["output_sha256"]),
        label="expanded schedule database",
    )
    return {
        "acquisition_lock_sha256": _digest(lock_path),
        "total_verified_bytes": total_bytes,
        "vehicle_object_count": vehicle_objects,
        "vehicle_row_count": vehicle_rows,
    }


def _referenced_files(payload: object) -> list[tuple[str, str, int | None]]:
    references: list[tuple[str, str, int | None]] = []
    if isinstance(payload, dict):
        path = payload.get("path")
        sha256 = payload.get("sha256")
        if isinstance(path, str) and isinstance(sha256, str) and len(sha256) == 64:
            raw_size = payload.get("bytes")
            references.append((path, sha256, int(raw_size) if isinstance(raw_size, int) else None))
        for value in payload.values():
            references.extend(_referenced_files(value))
    elif isinstance(payload, list):
        for value in payload:
            references.extend(_referenced_files(value))
    return references


def verify_manifest_tree(
    root: Path, manifest_path: Path, *, expected_hash: str, label: str
) -> dict[str, Any]:
    if _digest(manifest_path) != expected_hash:
        raise ReproductionError(f"{label} manifest hash changed")
    references = sorted(set(_referenced_files(_load(manifest_path))))
    if not references:
        raise ReproductionError(f"{label} manifest has no content references")
    total_bytes = 0
    for relative, sha256, size in references:
        total_bytes += _verify_file(
            root / relative,
            size=size,
            sha256=sha256,
            label=f"{label} output",
        )
    return {
        "manifest_sha256": expected_hash,
        "referenced_file_count": len(references),
        "verified_bytes": total_bytes,
    }


def _runtime_manifest(runtime_path: Path, key: str, root: Path) -> Path | None:
    if not runtime_path.is_file():
        return None
    payload = _load(runtime_path)
    relative = payload.get(key)
    expected = payload.get(key.replace("_path", "_sha256"))
    if not isinstance(relative, str) or not isinstance(expected, str):
        return None
    path = root / relative
    return path if path.is_file() and _digest(path) == expected else None


def _run(command: list[str]) -> None:
    completed = subprocess.run(  # noqa: S603 - fixed executable and repository-owned arguments
        command,
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise ReproductionError(
            f"reproduction command failed: {' '.join(command)}\n"
            f"{(completed.stdout + completed.stderr)[-4000:]}"
        )


def execute_pipeline(data_root: Path, rebuild_root: Path, frozen_runtime: Path) -> dict[str, str]:
    arrive90 = shutil.which("arrive90")
    if arrive90 is None:
        raise ReproductionError("arrive90 CLI is unavailable")
    normalized = rebuild_root / "normalized"
    dataset = rebuild_root / "datasets/travel-time-v1"
    model = rebuild_root / "models/travel-time-v1/primary"
    runtime = rebuild_root / "runtime"
    schedule = data_root / "raw/mbta-gtfs/2024/GTFS_ARCHIVE.db"
    actions: dict[str, str] = {"acquisition": "VERIFIED_IMMUTABLE_LOCK"}

    normalized_run = runtime / "normalization/normalization-run.json"
    if _runtime_manifest(normalized_run, "dataset_manifest_path", normalized) is None:
        _run(
            [
                arrive90,
                "data",
                "normalize",
                "--year",
                "2024",
                "--raw-root",
                str(data_root / "raw"),
                "--normalized-root",
                str(normalized),
                "--runtime-root",
                str(runtime / "normalization"),
            ]
        )
        actions["normalization"] = "REBUILT"
    else:
        actions["normalization"] = "VERIFIED_NOOP"

    population_run = runtime / "dataset/model-population-run.json"
    if _runtime_manifest(population_run, "manifest_path", dataset) is None:
        _run(
            [
                arrive90,
                "data",
                "build-dataset",
                "--normalized-root",
                str(normalized),
                "--dataset-root",
                str(dataset),
                "--schedule-database",
                str(schedule),
                "--runtime-root",
                str(runtime / "dataset"),
            ]
        )
        actions["episode_and_dataset_generation"] = "REBUILT"
    else:
        actions["episode_and_dataset_generation"] = "VERIFIED_NOOP"

    training_run = runtime / "training/training-run.json"
    model_ready = False
    if training_run.is_file():
        payload = _load(training_run)
        index_hash = payload.get("registry_index_sha256")
        if isinstance(index_hash, str):
            model_ready = bool(list(model.glob(f"registry-index-{index_hash}.json")))
    if not model_ready:
        _run(
            [
                arrive90,
                "model",
                "train",
                "--dataset-root",
                str(dataset),
                "--normalized-root",
                str(normalized),
                "--model-root",
                str(model),
                "--runtime-root",
                str(runtime / "training"),
            ]
        )
        actions["training"] = "REBUILT"
    else:
        actions["training"] = "VERIFIED_NOOP"

    rebuilt_report = runtime / "evaluation/rebuilt-report.json"
    expected_report = ROOT / "artifacts/reports/final/travel-time-v1.2.json"
    if not rebuilt_report.is_file() or rebuilt_report.read_bytes() != expected_report.read_bytes():
        prediction_manifest = next(frozen_runtime.glob("final-prediction-manifest-*.json"))
        protocol = next(frozen_runtime.glob("evaluation-freeze-*.json"))
        rebuilt_report.parent.mkdir(parents=True, exist_ok=True)
        _run(
            [
                arrive90,
                "evaluate",
                "rebuild",
                "--prediction-manifest",
                str(prediction_manifest),
                "--protocol",
                str(protocol),
                "--existing-report",
                str(expected_report),
                "--output",
                str(rebuilt_report),
                "--dataset-root",
                str(dataset),
                "--normalized-root",
                str(normalized),
                "--model-root",
                str(model),
            ]
        )
        actions["frozen_evaluation_rebuild"] = "REBUILT"
    else:
        actions["frozen_evaluation_rebuild"] = "VERIFIED_NOOP"
    return actions


def terminal_manifest(data_root: Path, *, products_root: Path | None = None) -> dict[str, Any]:
    acquisition = verify_acquisition(data_root)
    normalized_hash = _gate_hash("milestone-1", "dataset_manifest")
    population_hash = _gate_hash("milestone-2", "population_manifest")
    unsampled_hash = _gate_hash("milestone-2", "unsampled_manifest")
    model_index_hash = _gate_hash("milestone-3", "primary_registry_index")
    products = products_root or data_root
    normalized_root = products / "normalized"
    dataset_root = products / "datasets/travel-time-v1"
    model_root = products / "models/travel-time-v1/primary"
    normalized_manifest = _single_hash_path(
        normalized_root / "manifests/2024", "dataset-manifest-*.json", normalized_hash
    )
    population_manifest = _single_hash_path(
        dataset_root / "manifests", "model-population-manifest-*.json", population_hash
    )
    unsampled_manifest = _single_hash_path(
        dataset_root / "manifests", "unsampled-audit-manifest-*.json", unsampled_hash
    )
    model_index = _single_hash_path(model_root, "registry-index-*.json", model_index_hash)
    normalized = verify_manifest_tree(
        normalized_root,
        normalized_manifest,
        expected_hash=normalized_hash,
        label="normalized year",
    )
    unsampled = verify_manifest_tree(
        dataset_root,
        unsampled_manifest,
        expected_hash=unsampled_hash,
        label="unsampled dataset",
    )
    population = verify_manifest_tree(
        dataset_root,
        population_manifest,
        expected_hash=population_hash,
        label="selected model population",
    )
    model_index_payload = _load(model_index)
    entries = model_index_payload.get("entries")
    if not isinstance(entries, list) or len(entries) != 7:
        raise ReproductionError("model registry must contain seven final-compared bundles")
    root_artifacts = {
        "point-baselines": str(model_index_payload["point_baseline_sha256"]),
        "selection-freeze": str(model_index_payload["selection_freeze_sha256"]),
        "validation-comparison": str(model_index_payload["validation_comparison_sha256"]),
    }
    for prefix, artifact_hash in root_artifacts.items():
        _verify_file(
            model_root / f"{prefix}-{artifact_hash}.json",
            size=None,
            sha256=artifact_hash,
            label=f"model registry {prefix}",
        )
    verified_model_bytes = model_index.stat().st_size
    for entry in entries:
        if not isinstance(entry, dict):
            raise ReproductionError("model registry entry is invalid")
        relative = str(entry["registry_path"])
        manifest_hash = str(entry["manifest_sha256"])
        bundle_root = model_root / relative
        verified_model_bytes += _verify_file(
            bundle_root / "manifest.json",
            size=None,
            sha256=manifest_hash,
            label="model bundle manifest",
        )
        bundle = _load(bundle_root / "manifest.json")
        verified_model_bytes += _verify_file(
            bundle_root / "model.ubj",
            size=None,
            sha256=str(bundle["model_sha256"]),
            label="model bytes",
        )
        verified_model_bytes += _verify_file(
            bundle_root / "calibration.json",
            size=None,
            sha256=str(bundle["calibrator_sha256"]),
            label="calibration artifact",
        )
        verified_model_bytes += _verify_file(
            bundle_root / "model-manifest.json",
            size=None,
            sha256=str(bundle["model_wrapper_manifest_sha256"]),
            label="model wrapper manifest",
        )
    final_report = ROOT / "artifacts/reports/final/travel-time-v1.2.json"
    claims = ROOT / "artifacts/reports/claims/travel-time-v1.2.json"
    demo = ROOT / "artifacts/demo/travel-time-v1/terminal-manifest.json"
    checks = {
        "all_368_vehicle_objects_and_schedule_verified": (
            acquisition["vehicle_object_count"] == 368
        ),
        "all_366_service_days_reproduced": (
            _load(population_manifest).get("summary", {}).get("service_day_count") == 366
        ),
        "final_report_and_claims_are_hash_bound": (
            _load(claims).get("final_report_sha256") == _digest(final_report)
        ),
        "milestone_5_terminal_manifest_remains_valid": _load(demo).get("state") == "PASSED",
        "model_registry_has_seven_verified_bundles": len(entries) == 7,
        "normalized_dataset_and_population_manifests_verified": all(
            item["referenced_file_count"] > 0 for item in (normalized, unsampled, population)
        ),
    }
    return {
        "acceptance_version": "travel-time-v1.2",
        "checks": checks,
        "content": {
            "acquisition": acquisition,
            "final_report_sha256": _digest(final_report),
            "model_registry_index_sha256": model_index_hash,
            "model_registry_verified_bytes": verified_model_bytes,
            "normalized": normalized,
            "population": population,
            "unsampled": unsampled,
        },
        "state": "PASSED" if all(checks.values()) else "FAILED",
        "version": "full-year-reproduction-terminal-v1",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=ROOT / "data")
    parser.add_argument("--expected", type=Path, default=EXPECTED)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--write-expected", action="store_true")
    parser.add_argument("--rebuild-root", type=Path)
    parser.add_argument("--frozen-evaluation-runtime", type=Path)
    parser.add_argument("--execution-report", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    actions: dict[str, str] | None = None
    if args.rebuild_root is not None:
        if args.frozen_evaluation_runtime is None:
            raise ReproductionError("--frozen-evaluation-runtime is required for a rebuild")
        actions = execute_pipeline(
            args.data_root, args.rebuild_root, args.frozen_evaluation_runtime
        )
    manifest = terminal_manifest(args.data_root, products_root=args.rebuild_root)
    body = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(body, encoding="utf-8")
    if args.write_expected:
        args.expected.parent.mkdir(parents=True, exist_ok=True)
        args.expected.write_text(body, encoding="utf-8")
    elif not args.expected.is_file() or args.expected.read_text(encoding="utf-8") != body:
        raise ReproductionError(
            "full-year terminal manifest differs from the committed expectation"
        )
    if args.execution_report is not None:
        if actions is None:
            raise ReproductionError("an execution report requires --rebuild-root")
        report = {
            "actions": actions,
            "all_stages_rebuilt_or_verified": all(
                value in {"REBUILT", "VERIFIED_IMMUTABLE_LOCK", "VERIFIED_NOOP"}
                for value in actions.values()
            ),
            "terminal_manifest_sha256": hashlib.sha256(body.encode()).hexdigest(),
            "version": "full-year-reproduction-execution-v1",
        }
        args.execution_report.parent.mkdir(parents=True, exist_ok=True)
        args.execution_report.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    print(body, end="")
    return 0 if manifest["state"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
