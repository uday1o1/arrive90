"""Measure candidate and replay mechanics for the frozen Milestone 6 workload."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import statistics
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from datetime import time as wall_time
from functools import partial
from pathlib import Path
from typing import Any

from arrive90_data_contracts.candidates import CandidateItinerary, TransitLeg
from arrive90_routing.candidates import deduplicate_and_limit
from arrive90_routing.population import PopulationConfig, StationPair, generate_query_population

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2025, 1, 1, 12, tzinfo=UTC)


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * percentile)))
    return ordered[index]


def _measure(
    operation: Callable[[], object], *, iterations: int
) -> tuple[dict[str, float], object]:
    samples: list[float] = []
    last: object = None
    for _index in range(iterations):
        started = time.perf_counter_ns()
        last = operation()
        samples.append((time.perf_counter_ns() - started) / 1_000_000)
    mean = statistics.fmean(samples)
    return (
        {
            "mean_ms": mean,
            "p50_ms": _percentile(samples, 0.50),
            "p95_ms": _percentile(samples, 0.95),
            "p99_ms": _percentile(samples, 0.99),
            "throughput_per_second": 1_000 / mean,
        },
        last,
    )


def _candidate(index: int) -> CandidateItinerary:
    leg = TransitLeg(
        f"pattern-{index}",
        "route",
        0,
        f"trip-{index}",
        "a-platform",
        "a",
        "b-platform",
        "b",
        NOW + timedelta(seconds=index),
        NOW + timedelta(minutes=10, seconds=index),
        ("a-platform", "b-platform"),
    )
    return CandidateItinerary((leg,), ())


def _replay(days: int) -> object:
    dates = tuple(date(2025, 1, 1) + timedelta(days=index) for index in range(days))
    config = PopulationConfig(
        maximum_pairs_per_stratum=1,
        readiness_horizons_minutes=(0,),
        query_start_local=wall_time(12),
        query_end_local=wall_time(12),
    )
    return generate_query_population(
        (StationPair("a", "b", "direct"),),
        dates,
        schedule_version_by_date=dict.fromkeys(dates, "schedule-v1"),
        split_by_date=dict.fromkeys(dates, "benchmark"),
        config=config,
    )


def _host_visible_memory_bytes() -> int | None:
    path = Path("/proc/meminfo")
    if not path.is_file():
        return None
    return int(path.read_text(encoding="utf-8").splitlines()[0].split()[1]) * 1024


def _cgroup_cpu_allocation() -> float | None:
    path = Path("/sys/fs/cgroup/cpu.max")
    if not path.is_file():
        return None
    quota, period = path.read_text(encoding="utf-8").split()
    return None if quota == "max" else int(quota) / int(period)


def _cgroup_memory_limit_bytes() -> int | None:
    path = Path("/sys/fs/cgroup/memory.max")
    if not path.is_file():
        return None
    value = path.read_text(encoding="utf-8").strip()
    return None if value == "max" else int(value)


def build_report() -> dict[str, Any]:
    candidate_results: dict[str, dict[str, float]] = {}
    for count in (1, 5, 10):
        candidates = tuple(_candidate(index) for index in range(count))
        result, normalized = _measure(
            partial(deduplicate_and_limit, candidates),
            iterations=500,
        )
        if len(normalized) != count:  # type: ignore[arg-type]
            raise RuntimeError("candidate benchmark changed the frozen candidate set")
        candidate_results[str(count)] = result

    replay_results: dict[str, dict[str, Any]] = {}
    replay_manifest = hashlib.sha256()
    for label, days, iterations in (
        ("one_day", 1, 20),
        ("one_month", 31, 10),
        ("one_year", 365, 5),
    ):
        result, population = _measure(partial(_replay, days), iterations=iterations)
        manifest_hash = population.manifest_hash  # type: ignore[attr-defined]
        repeated_hash = _replay(days).manifest_hash  # type: ignore[attr-defined]
        replay_manifest.update(f"{label}:{manifest_hash}".encode())
        replay_results[label] = {
            **result,
            "base_query_count": len(population.base_queries),  # type: ignore[attr-defined]
            "deadline_variant_count": len(population.deadline_variants),  # type: ignore[attr-defined]
            "deterministic_manifest": manifest_hash == repeated_hash,
            "manifest_hash": manifest_hash,
            "service_days": days,
        }

    api_path = ROOT / "artifacts/reports/qualification/milestone-5-latency.json"
    api = json.loads(api_path.read_text(encoding="utf-8"))
    hardware_match = (
        platform.system() == "Linux"
        and platform.machine() == "aarch64"
        and _cgroup_cpu_allocation() == 4
        and _cgroup_memory_limit_bytes() == 8_307_167_232
    )
    checks = {
        "api_benchmark_passed_on_named_hardware": api.get("status") == "PASSED",
        "candidate_p95_below_cached_search_limit": max(
            result["p95_ms"] for result in candidate_results.values()
        )
        < 1_000,
        "named_reference_hardware_matches": hardware_match,
        "replay_is_deterministic_at_all_temporal_scales": all(
            result["deterministic_manifest"] for result in replay_results.values()
        ),
    }
    return {
        "api_benchmark_report_sha256": hashlib.sha256(api_path.read_bytes()).hexdigest(),
        "candidate_generation": candidate_results,
        "checks": checks,
        "environment": {
            "benchmark_image_id": os.environ.get("ARRIVE90_BENCHMARK_IMAGE_ID"),
            "base_image": (
                "python@sha256:6c4dd321d176d61ea848dc8c73a4f7dbae8f70e0ee48bb411ea2f045b599fa8e"
            ),
            "cgroup_cpu_allocation": _cgroup_cpu_allocation(),
            "cgroup_memory_limit_bytes": _cgroup_memory_limit_bytes(),
            "host_visible_cpu_count": os.cpu_count(),
            "host_visible_memory_bytes": _host_visible_memory_bytes(),
            "machine": platform.machine(),
            "operating_system": platform.platform(),
            "python": platform.python_version(),
        },
        "failing_checks": sorted(key for key, passed in checks.items() if not passed),
        "replay_generation": replay_results,
        "status": "PASSED" if all(checks.values()) else "INSUFFICIENT_EVIDENCE",
        "workload": {
            "candidate_counts": [1, 5, 10],
            "deadline_slacks_minutes": list(range(5, 181, 5)),
            "origin_destination_pairs": 1,
            "query_times_per_service_day": 1,
            "readiness_horizons_minutes": [0],
            "replay_manifest_hash": replay_manifest.hexdigest(),
            "service_day_scales": [1, 31, 365],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = build_report()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(args.output)
    return 0 if report["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
