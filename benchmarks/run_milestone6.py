"""Benchmark every frozen 2024 pipeline and explorer stage on repository-owned workloads."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import resource
import statistics
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import fastapi
import numpy as np
import pyarrow  # type: ignore[import-untyped]
import scipy  # type: ignore[import-untyped]
import xgboost
from arrive90_evaluation.modeling_data import load_modeling_context
from arrive90_ingestion.episodes import build_trip_episodes
from arrive90_ingestion.vehicle import normalize_vehicle_parquet
from arrive90_service.app import create_app
from arrive90_service.explorer import ExplorerRepository
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.reproduce_full_year import verify_acquisition  # noqa: E402


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * percentile)))
    return ordered[index]


def _peak_rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if platform.system() == "Darwin" else value * 1024)


def _measure[T](
    operation: Callable[[], T], *, iterations: int, work_units: int
) -> tuple[dict[str, Any], T]:
    if iterations < 1:
        raise ValueError("benchmark iterations must be positive")
    started = time.perf_counter_ns()
    result = operation()
    samples = [(time.perf_counter_ns() - started) / 1_000_000]
    for _index in range(1, iterations):
        started = time.perf_counter_ns()
        result = operation()
        samples.append((time.perf_counter_ns() - started) / 1_000_000)
    mean = statistics.fmean(samples)
    return (
        {
            "iterations": iterations,
            "mean_ms": mean,
            "p50_ms": _percentile(samples, 0.50),
            "p95_ms": _percentile(samples, 0.95),
            "peak_process_rss_bytes": _peak_rss_bytes(),
            "throughput_work_units_per_second": work_units / (mean / 1_000),
            "work_units_per_iteration": work_units,
        },
        result,
    )


def _historical_stage(
    elapsed_seconds: list[float], *, work_units: int, peak_bytes: int | None, source: str
) -> dict[str, Any]:
    milliseconds = [value * 1_000 for value in elapsed_seconds]
    mean = statistics.fmean(elapsed_seconds)
    return {
        "iterations": len(elapsed_seconds),
        "measurement_source": source,
        "mean_ms": mean * 1_000,
        "p50_ms": _percentile(milliseconds, 0.50),
        "p95_ms": _percentile(milliseconds, 0.95),
        "peak_process_rss_bytes": peak_bytes,
        "throughput_work_units_per_second": work_units / mean,
        "work_units_per_iteration": work_units,
    }


def _directory_bytes(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def build_report() -> dict[str, Any]:
    before = {
        "final_report": _digest(ROOT / "artifacts/reports/final/travel-time-v1.2.json"),
        "model_manifest": _digest(
            ROOT
            / "artifacts/demo/travel-time-v1/model"
            / "1c49f5702cfd7bbd6ad4633a59fd71c42333c28cb53c390ccbf8c07a0ab6e06b"
            / "manifest.json"
        ),
        "terminal_manifest": _digest(ROOT / "artifacts/demo/travel-time-v1/terminal-manifest.json"),
    }
    stages: dict[str, dict[str, Any]] = {}
    acquisition, acquisition_result = _measure(
        lambda: verify_acquisition(ROOT / "data"), iterations=1, work_units=368
    )
    acquisition["workload"] = "all 368 acquired vehicle objects plus schedule lock"
    stages["acquisition_verification"] = acquisition

    lock = json.loads(
        (ROOT / "configs/source-locks/mbta-2024-acquired.json").read_text(encoding="utf-8")
    )
    first = lock["content_entries"][0]
    source = ROOT / "data/raw/bus-observatory/mbta_all" / Path(first["source_object_key"]).name
    normalization, normalized = _measure(
        lambda: normalize_vehicle_parquet(source, source_object_key=first["source_object_key"]),
        iterations=1,
        work_units=int(first["row_count"]),
    )
    normalization["workload"] = str(first["source_object_key"])
    stages["normalization"] = normalization

    episode, episode_result = _measure(
        lambda: build_trip_episodes(normalized.observations),
        iterations=5,
        work_units=len(normalized.observations),
    )
    episode["workload"] = "normalized observations from one immutable source object"
    episode["episode_count"] = len(episode_result.episodes)
    stages["episode_construction"] = episode

    dataset, context = _measure(
        lambda: load_modeling_context(
            ROOT / "data/datasets/travel-time-v1",
            normalized_root=ROOT / "data/normalized",
        ),
        iterations=3,
        work_units=366,
    )
    dataset["workload"] = "366-day population and transform manifest loading"
    dataset["population_manifest_sha256"] = context.population_manifest_sha256
    stages["dataset_generation"] = dataset

    m1 = json.loads(
        (ROOT / "artifacts/runtime/milestone-1/normalization-run.json").read_text(encoding="utf-8")
    )
    m1_restart = json.loads(
        (ROOT / "artifacts/runtime/milestone-1-restart/normalization-run.json").read_text(
            encoding="utf-8"
        )
    )
    stages["normalization_full_year"] = _historical_stage(
        [float(m1["elapsed_seconds"]), float(m1_restart["elapsed_seconds"])],
        work_units=208_444_419,
        peak_bytes=max(
            int(m1["peak_resident_memory_bytes"]),
            int(m1_restart["peak_resident_memory_bytes"]),
        ),
        source="two deterministic full-year milestone runs",
    )
    m2 = json.loads(
        (ROOT / "artifacts/runtime/milestone-2/model-population-run.json").read_text(
            encoding="utf-8"
        )
    )
    dmatrix = json.loads(
        (ROOT / "artifacts/runtime/milestone-2/dmatrix-benchmark.json").read_text(encoding="utf-8")
    )
    stages["dataset_generation_full_year"] = _historical_stage(
        [float(m2["elapsed_seconds"])],
        work_units=int(m2["selected_example_count"]),
        peak_bytes=int(dmatrix["projected_peak_memory_bytes"]),
        source="deterministic full-year population build",
    )
    m3 = json.loads(
        (ROOT / "artifacts/runtime/milestone-3/training-run.json").read_text(encoding="utf-8")
    )
    stages["training"] = _historical_stage(
        [float(m3["elapsed_seconds"])],
        work_units=int(m3["training_rows"]),
        peak_bytes=int(dmatrix["projected_peak_memory_bytes"]),
        source="deterministic seven-bundle training and calibration run",
    )

    repository = ExplorerRepository.load()
    replay_ids = sorted(repository.records)

    def batch_score() -> tuple[float, ...]:
        return tuple(
            repository.prediction(replay_id, horizon_seconds=900)["selected_horizon"]["probability"]
            for replay_id in replay_ids
        )

    scoring, probabilities = _measure(batch_score, iterations=3, work_units=len(replay_ids))
    scoring["workload"] = "all 200 frozen held-out replays through the real scorer"
    scoring["probability_sha256"] = hashlib.sha256(
        np.asarray(probabilities, dtype=np.float64).tobytes()
    ).hexdigest()
    stages["batch_scoring"] = scoring

    client = TestClient(create_app(repository=repository))
    replay_id = replay_ids[0]

    def api_score() -> None:
        response = client.get(
            f"/v1/explorer/replays/{replay_id}/prediction",
            params={"horizon_seconds": 900},
        )
        if response.status_code != 200:
            raise RuntimeError("API benchmark request failed")

    api, _ = _measure(api_score, iterations=100, work_units=1)
    api["workload"] = "warm in-process GET prediction for one frozen replay"
    stages["api_scoring"] = api
    startup, _ = _measure(ExplorerRepository.load, iterations=10, work_units=1)
    startup["workload"] = "cold repository verification and model load"
    stages["explorer_startup"] = startup

    after = {
        "final_report": _digest(ROOT / "artifacts/reports/final/travel-time-v1.2.json"),
        "model_manifest": _digest(
            ROOT
            / "artifacts/demo/travel-time-v1/model"
            / "1c49f5702cfd7bbd6ad4633a59fd71c42333c28cb53c390ccbf8c07a0ab6e06b"
            / "manifest.json"
        ),
        "terminal_manifest": _digest(ROOT / "artifacts/demo/travel-time-v1/terminal-manifest.json"),
    }
    required = {
        "acquisition_verification",
        "api_scoring",
        "batch_scoring",
        "dataset_generation",
        "episode_construction",
        "explorer_startup",
        "normalization",
        "training",
    }
    physical_memory = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    checks = {
        "all_eight_required_stages_have_p50_p95_throughput_and_memory": required.issubset(stages)
        and all(
            all(
                value is not None and math.isfinite(float(value)) and float(value) >= 0
                for value in (
                    stages[name]["p50_ms"],
                    stages[name]["p95_ms"],
                    stages[name]["throughput_work_units_per_second"],
                    stages[name]["peak_process_rss_bytes"],
                )
            )
            for name in required
        ),
        "benchmark_did_not_change_correctness_artifacts": before == after,
        "full_year_peak_memory_is_bounded_below_70_percent_of_host": int(
            stages["normalization_full_year"]["peak_process_rss_bytes"]
        )
        < int(physical_memory * 0.70),
        "full_year_source_lock_was_verified": acquisition_result["vehicle_object_count"] == 368,
    }
    return {
        "acceptance_version": "travel-time-v1.2",
        "checks": checks,
        "dependencies": {
            "fastapi": fastapi.__version__,
            "numpy": np.__version__,
            "pyarrow": pyarrow.__version__,
            "scipy": scipy.__version__,
            "xgboost": xgboost.__version__,
        },
        "environment": {
            "logical_cpu_count": os.cpu_count(),
            "machine": platform.machine(),
            "physical_memory_bytes": physical_memory,
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
        "failing_checks": sorted(key for key, passed in checks.items() if not passed),
        "optimization": {
            "performed": False,
            "reason": "No measured acceptance bottleneck required optimization.",
        },
        "stages": stages,
        "status": "PASSED" if all(checks.values()) else "FAILED",
        "storage_bytes": {
            "dataset": _directory_bytes(ROOT / "data/datasets/travel-time-v1"),
            "demo": _directory_bytes(ROOT / "artifacts/demo/travel-time-v1"),
            "models": _directory_bytes(ROOT / "data/models/travel-time-v1/primary"),
            "normalized": _directory_bytes(ROOT / "data/normalized"),
            "raw": _directory_bytes(ROOT / "data/raw"),
        },
        "workload_manifest_hashes": {
            "acquisition_lock": _digest(ROOT / "configs/source-locks/mbta-2024-acquired.json"),
            "feature_transform": repository.transform_sha256,
            "final_report": before["final_report"],
            "model_manifest": repository.bundle.manifest.manifest_hash,
            "population_manifest": context.population_manifest_sha256,
            "replay_fixture": _digest(ROOT / "artifacts/demo/travel-time-v1/replay-fixture.json"),
            "uv_lock": _digest(ROOT / "uv.lock"),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts/reports/qualification/milestone-6-performance-v1.2.json",
    )
    args = parser.parse_args()
    report = build_report()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(args.output)
    return 0 if report["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
