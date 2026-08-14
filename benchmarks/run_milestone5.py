"""Measure the frozen Milestone 5 local decision and API workload."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import statistics
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import partial
from pathlib import Path
from typing import Any

import fastapi
import httpx2
from arrive90_data_contracts.candidates import CandidateItinerary, TransitLeg
from arrive90_decision.contracts import (
    CandidateScore,
    DecisionContext,
    EligibilityManifest,
    HorizonSupportManifest,
    InitialDecisionRequest,
    RecoveryTriggerInput,
    ScoringState,
    TripState,
)
from arrive90_decision.initial import select_initial_decision
from arrive90_decision.recovery import select_recovery_decision
from arrive90_service.app import create_app
from arrive90_service.contracts import ServiceConfig
from arrive90_service.demo import LocalBlockedBackend
from arrive90_service.store import CapabilityTripStore
from fastapi.testclient import TestClient

NOW = datetime(2025, 1, 1, 12, tzinfo=UTC)
DecisionFixture = tuple[
    tuple[CandidateScore, ...],
    InitialDecisionRequest,
    DecisionContext,
    EligibilityManifest,
    HorizonSupportManifest,
    ScoringState,
]


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * percentile)))
    return ordered[index]


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


def _decision_fixture(count: int, state: ScoringState) -> DecisionFixture:
    scores = tuple(
        CandidateScore(
            _candidate(index),
            0.80 + index / 100,
            f"band-{index}",
            ("line", "station-a", "station-b"),
        )
        for index in range(count)
    )
    cells = frozenset(
        cell
        for score in scores
        for cell in (score.prediction_band_cell_id, *score.applicable_slice_cell_ids)
    )
    return (
        scores,
        InitialDecisionRequest(
            NOW,
            NOW + timedelta(minutes=30),
            Decimal("0.90"),
            1_200,
            "slack-30",
        ),
        DecisionContext(
            NOW,
            "benchmark-context",
            "ALERT_MASK_V1",
            "benchmark-manifest",
            tuple((score.itinerary.policy_key, True) for score in scores),
        ),
        EligibilityManifest(cells, cells),
        HorizonSupportManifest(frozenset({"slack-30"})),
        state,
    )


def _duration_samples(operation: Callable[[], object], *, iterations: int = 500) -> list[float]:
    for _index in range(20):
        operation()
    samples: list[float] = []
    for _index in range(iterations):
        started = time.perf_counter_ns()
        operation()
        samples.append((time.perf_counter_ns() - started) / 1_000_000)
    return samples


def _summary(samples: list[float]) -> dict[str, float]:
    return {
        "mean_ms": statistics.fmean(samples),
        "p50_ms": _percentile(samples, 0.50),
        "p95_ms": _percentile(samples, 0.95),
        "p99_ms": _percentile(samples, 0.99),
        "throughput_per_second": 1_000 / statistics.fmean(samples),
    }


def _memory_bytes() -> int | None:
    path = Path("/proc/meminfo")
    if not path.is_file():
        return None
    first = path.read_text(encoding="utf-8").splitlines()[0]
    return int(first.split()[1]) * 1024


def build_report() -> dict[str, Any]:
    decision_results: dict[str, dict[str, float]] = {}
    manifest = hashlib.sha256()
    for count in (1, 5, 10):
        for state in (ScoringState.READY, ScoringState.STALE, ScoringState.ABSTAINED):
            scores, request, context, eligibility, horizon, scoring_state = _decision_fixture(
                count, state
            )
            manifest.update("|".join(score.itinerary.policy_key for score in scores).encode())

            decide = partial(
                select_initial_decision,
                scores,
                request=request,
                context=context,
                eligibility=eligibility,
                horizon_support=horizon,
                scoring_state=scoring_state,
            )

            decision_results[f"{count}-{state.value.lower()}"] = _summary(_duration_samples(decide))
    recovery_candidates = tuple(_candidate(index) for index in range(10))
    recovery_context = DecisionContext(
        NOW,
        "benchmark-recovery",
        "ALERT_MASK_V1",
        "benchmark-recovery-manifest",
        tuple((candidate.policy_key, True) for candidate in recovery_candidates),
    )
    recovery_trigger = RecoveryTriggerInput(
        TripState.AT_TRANSFER,
        True,
        None,
        False,
        False,
        False,
        True,
        False,
    )

    recover = partial(
        select_recovery_decision,
        recovery_candidates,
        continuation_policy_key=recovery_candidates[0].policy_key,
        context=recovery_context,
        trigger=recovery_trigger,
    )

    recovery_result = _summary(_duration_samples(recover))
    config = ServiceConfig(
        allowed_hosts=frozenset({"testserver"}),
        allowed_origins=frozenset({"http://testserver"}),
        decision_keys=(("benchmark", b"d" * 32),),
        active_decision_key_version="benchmark",
        trip_keys=(("benchmark", b"t" * 32),),
        active_trip_key_version="benchmark",
        search_limit_per_minute=100_000,
    )
    store = CapabilityTripStore(":memory:", config)
    app = create_app(
        backend=LocalBlockedBackend(),
        store=store,
        config=config,
        clock=lambda: NOW,
        epoch_clock=lambda: 100.0,
    )
    client = TestClient(app)
    payload = {
        "deadline": (NOW + timedelta(minutes=30)).isoformat(),
        "destination_station_id": "demo-destination",
        "maximum_extra_minutes": 20,
        "origin_station_id": "demo-origin",
        "ready_at": NOW.isoformat(),
        "reliability_target": "0.90",
    }

    def api_search() -> None:
        response = client.post(
            "/v1/journeys/search",
            json=payload,
            headers={"Origin": "http://testserver"},
        )
        if response.status_code != 200:
            raise RuntimeError("benchmark API request failed")

    api_result = _summary(_duration_samples(api_search, iterations=200))
    store.close()
    decision_p95 = max(item["p95_ms"] for item in decision_results.values())
    hardware_match = (
        platform.system() == "Linux"
        and platform.machine() == "aarch64"
        and os.cpu_count() == 4
        and _memory_bytes() == 8_307_167_232
    )
    checks = {
        "cached_schedule_search_p95_below_1000_ms": api_result["p95_ms"] < 1_000,
        "decision_p95_below_100_ms": decision_p95 < 100,
        "named_reference_hardware_matches": hardware_match,
        "recovery_p95_below_100_ms": recovery_result["p95_ms"] < 100,
    }
    return {
        "checks": checks,
        "decision_by_candidate_count_and_feed_state": decision_results,
        "environment": {
            "benchmark_image_id": os.environ.get("ARRIVE90_BENCHMARK_IMAGE_ID"),
            "base_image": (
                "python@sha256:6c4dd321d176d61ea848dc8c73a4f7dbae8f70e0ee48bb411ea2f045b599fa8e"
            ),
            "cpu_count": os.cpu_count(),
            "fastapi": fastapi.__version__,
            "httpx2": httpx2.__version__,
            "machine": platform.machine(),
            "memory_bytes": _memory_bytes(),
            "operating_system": platform.platform(),
            "python": platform.python_version(),
        },
        "failing_checks": sorted(key for key, passed in checks.items() if not passed),
        "iterations": {"api": 200, "decision_each": 500, "recovery": 500},
        "recovery": recovery_result,
        "status": "PASSED" if all(checks.values()) else "INSUFFICIENT_EVIDENCE",
        "warm_cached_api_search": api_result,
        "workload_manifest_hash": manifest.hexdigest(),
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
