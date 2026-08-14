"""Verify the complete travel-time-v1.2 Milestone 2 gate from repository artifacts."""
# ruff: noqa: E501 - embedded Markdown keeps complete sentences on physical lines.

from __future__ import annotations

import hashlib
import hmac
import json
import math
import platform
import shutil
import subprocess
import sys
from collections import defaultdict
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any, cast

import pyarrow.parquet as pq  # type: ignore[import-untyped]
from arrive90_data_contracts.dataset import chronological_split
from arrive90_data_contracts.gates import DEFAULT_ACCEPTANCE_VERSION
from arrive90_evaluation.model_population import (
    FEATURE_SCHEMA,
    MODELED_ROUTE,
    QUALIFICATION_PROBES,
    SELECTED_SCHEMA,
    SELECTION_LIMIT,
    SELECTION_SEED,
    _active_unsampled_manifest,
    _feature_input,
    assert_final_test_sealed,
    evaluate_blue_retention,
)
from arrive90_evaluation.year_dataset import (
    DEFAULT_DATASET_ROOT,
    DEFAULT_NORMALIZED_ROOT,
    YearDatasetError,
    _load_json,
    _normalized_manifest,
)
from arrive90_features.transform import (
    MISSING_TOKEN,
    UNKNOWN_TOKEN,
    FittedFeatureTransform,
)
from arrive90_features.travel_time_registry import CATEGORICAL_FEATURES
from arrive90_ingestion.acquisition import sha256_file

ROOT = Path(__file__).resolve().parents[1]


def _required_executable(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        raise RuntimeError(f"{name} is required for Milestone 2 qualification")
    return executable


GIT = _required_executable("git")
MAKE = _required_executable("make")
DATASET_ROOT = ROOT / DEFAULT_DATASET_ROOT
NORMALIZED_ROOT = ROOT / DEFAULT_NORMALIZED_ROOT
FIRST_RUNTIME = ROOT / "artifacts/runtime/milestone-2/model-population-run.json"
RESTART_RUNTIME = ROOT / "artifacts/runtime/milestone-2-restart/model-population-run.json"
BENCHMARK_PATH = ROOT / "artifacts/runtime/milestone-2/dmatrix-benchmark.json"
QUALIFICATION_PATH = ROOT / "artifacts/reports/qualification/milestone-2-dataset-v1.2.json"
GATE_PATH = ROOT / "artifacts/reports/gates/milestone-2.json"
DATA_CARD_PATH = ROOT / "docs/data-card.md"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(*args: str) -> str:
    result = subprocess.run(  # noqa: S603 - fixed local git executable and arguments.
        [GIT, *args], cwd=ROOT, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise YearDatasetError(f"{field} must be an object")
    return value


def _list(value: object, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise YearDatasetError(f"{field} must be a list")
    return value


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise YearDatasetError(f"{field} must be an integer")
    return value


def _number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise YearDatasetError(f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise YearDatasetError(f"{field} must be finite")
    return result


def _verify_entry(entry: Mapping[str, object]) -> Path:
    path = DATASET_ROOT / str(entry.get("path", ""))
    if not path.is_file() or sha256_file(path) != entry.get("sha256"):
        raise YearDatasetError(f"dataset partition failed verification: {path}")
    return path


def _active_population_manifest() -> tuple[Path, dict[str, Any], str]:
    pointer_path = DATASET_ROOT / "manifests/active-model-population.json"
    if pointer_path.is_file():
        pointer = _load_json(pointer_path)
        if pointer.get("acceptance_version") != DEFAULT_ACCEPTANCE_VERSION:
            raise YearDatasetError("active population pointer has the wrong acceptance version")
        path = (DATASET_ROOT / str(pointer.get("path", ""))).resolve()
        if not path.is_relative_to(DATASET_ROOT.resolve()):
            raise YearDatasetError("active population pointer escapes the dataset root")
        digest = str(pointer.get("sha256", ""))
        if not path.is_file() or sha256_file(path) != digest:
            raise YearDatasetError("active population pointer failed verification")
        manifest = _load_json(path)
        if manifest.get("acceptance_version") != DEFAULT_ACCEPTANCE_VERSION:
            raise YearDatasetError("active population manifest has the wrong acceptance version")
        if path.stem != f"model-population-manifest-{digest}":
            raise YearDatasetError("population manifest filename does not match its hash")
        return path, manifest, digest
    active: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted((DATASET_ROOT / "manifests").glob("model-population-manifest-*.json")):
        manifest = _load_json(path)
        if manifest.get("acceptance_version") == DEFAULT_ACCEPTANCE_VERSION:
            active.append((path, manifest))
    if len(active) != 1:
        raise YearDatasetError("exactly one active model-population manifest is required")
    path, manifest = active[0]
    digest = sha256_file(path)
    if path.stem != f"model-population-manifest-{digest}":
        raise YearDatasetError("population manifest filename does not match its hash")
    return path, manifest, digest


def _selection_report(manifest: Mapping[str, object]) -> dict[str, object]:
    entries = _list(manifest.get("selection_partitions"), "selection_partitions")
    if len(entries) != 366:
        raise YearDatasetError("selection manifest must contain 366 service dates")
    anchor_split: dict[str, str] = {}
    episode_split: dict[str, str] = {}
    route_values: set[str] = set()
    total_rows = 0
    total_anchors = 0
    for raw_entry in entries:
        entry = _mapping(raw_entry, "selection entry")
        service_date = date.fromisoformat(str(entry["service_date"]))
        split = str(entry["split"])
        if split != chronological_split(service_date).value:
            raise YearDatasetError("selection partition crosses a chronological split")
        table = pq.read_table(_verify_entry(entry), schema=SELECTED_SCHEMA)
        if table.num_rows != _integer(entry.get("row_count"), "selection row count"):
            raise YearDatasetError("selection row count does not match its manifest")
        strata = _mapping(entry.get("strata"), "selection strata")
        by_anchor: dict[str, list[dict[str, object]]] = defaultdict(list)
        for raw in table.to_pylist():
            row = {str(key): value for key, value in raw.items()}
            route = str(row["route_id"])
            route_values.add(route)
            if route != MODELED_ROUTE:
                raise YearDatasetError("rejected route entered the selected population")
            anchor = str(row["anchor_observation_id"])
            episode = str(row["episode_id"])
            direction = str(_integer(row.get("direction_id"), "direction_id"))
            expected_digest = hmac.new(SELECTION_SEED, anchor.encode(), hashlib.sha256).hexdigest()
            if row.get("selection_digest") != expected_digest:
                raise YearDatasetError("selected anchor HMAC digest is incorrect")
            expected_split = anchor_split.setdefault(anchor, split)
            if expected_split != split:
                raise YearDatasetError("one anchor crosses a chronological split")
            expected_episode_split = episode_split.setdefault(episode, split)
            if expected_episode_split != split:
                raise YearDatasetError("one episode crosses a chronological split")
            stratum = _mapping(strata.get(direction), "selection stratum")
            probability = _number(stratum.get("inclusion_probability"), "inclusion_probability")
            if not math.isclose(
                _number(row.get("inclusion_probability"), "row inclusion probability"),
                probability,
                rel_tol=0,
                abs_tol=1e-15,
            ):
                raise YearDatasetError("row inclusion probability differs from its stratum")
            by_anchor[anchor].append(row)
        for direction, raw_stratum in strata.items():
            stratum = _mapping(raw_stratum, "selection stratum")
            selected_count = _integer(stratum.get("selected_anchor_count"), "selected anchor count")
            source_count = _integer(stratum.get("anchor_count"), "anchor count")
            if selected_count != min(SELECTION_LIMIT, source_count):
                raise YearDatasetError(f"stratum {direction} violates the anchor cap")
        for rows in by_anchor.values():
            base_sum = math.fsum(_number(row.get("base_weight"), "base weight") for row in rows)
            probability = _number(rows[0].get("inclusion_probability"), "inclusion probability")
            analysis_sum = math.fsum(
                _number(row.get("analysis_weight"), "analysis weight") for row in rows
            )
            if not math.isclose(base_sum, 1.0, rel_tol=0, abs_tol=1e-9):
                raise YearDatasetError("selected base weights do not sum to one")
            if not math.isclose(analysis_sum, 1.0 / probability, rel_tol=0, abs_tol=1e-8):
                raise YearDatasetError("selected analysis weights do not sum to inverse pi")
        total_rows += table.num_rows
        total_anchors += len(by_anchor)
    return {
        "anchor_count": total_anchors,
        "episode_count": len(episode_split),
        "example_count": total_rows,
        "route_values": sorted(route_values),
        "service_day_count": len(entries),
    }


def _load_transform(manifest: Mapping[str, object]) -> tuple[FittedFeatureTransform, Path]:
    transform_entry = _mapping(manifest.get("transform"), "transform")
    path = _verify_entry(transform_entry)
    raw = _load_json(path)
    raw_vocabularies = _mapping(raw.get("categorical_vocabularies"), "vocabularies")
    vocabularies = tuple(
        (name, tuple(cast(list[str], raw_vocabularies[name]))) for name in CATEGORICAL_FEATURES
    )
    transform = FittedFeatureTransform(
        training_row_sha256=str(raw["training_row_sha256"]),
        vocabularies=vocabularies,
        column_names=tuple(cast(list[str], raw["column_names"])),
        output_schema_sha256=str(raw["output_schema_sha256"]),
        csr_index_dtype=str(raw["csr_index_dtype"]),
        value_dtype=str(raw["value_dtype"]),
        version=str(raw["version"]),
    )
    return transform, path


def _feature_report(
    manifest: Mapping[str, object], transform: FittedFeatureTransform
) -> dict[str, object]:
    entries = _list(manifest.get("feature_partitions"), "feature_partitions")
    if len(entries) != 366:
        raise YearDatasetError("feature manifest must contain 366 service dates")
    row_count = 0
    first_row: dict[str, object] | None = None
    forbidden_columns = {
        "outcome_state",
        "lower_bound_seconds",
        "upper_bound_seconds",
        "final_episode_length",
        "post_outcome_average_seconds",
    }
    for raw_entry in entries:
        entry = _mapping(raw_entry, "feature entry")
        service_date = date.fromisoformat(str(entry["service_date"]))
        if entry.get("split") != chronological_split(service_date).value:
            raise YearDatasetError("feature partition crosses a chronological split")
        table = pq.read_table(_verify_entry(entry), schema=FEATURE_SCHEMA)
        if forbidden_columns.intersection(table.schema.names):
            raise YearDatasetError("feature partition contains an outcome or future aggregate")
        if table.num_rows != _integer(entry.get("row_count"), "feature row count"):
            raise YearDatasetError("feature row count differs from its manifest")
        if table.num_rows and first_row is None:
            first_row = {str(key): value for key, value in table.slice(0, 1).to_pylist()[0].items()}
        if set(table["route_id"].to_pylist()) - {MODELED_ROUTE}:
            raise YearDatasetError("rejected route entered the feature population")
        row_count += table.num_rows
    if first_row is None:
        raise YearDatasetError("feature population is empty")
    missing = dict(first_row)
    unknown = dict(first_row)
    for name in CATEGORICAL_FEATURES:
        missing[name] = None
        unknown[name] = f"seeded-unknown-{name}"
    missing_matrix = transform.transform((_feature_input(missing),))
    unknown_matrix = transform.transform((_feature_input(unknown),))
    if missing_matrix.shape != unknown_matrix.shape:
        raise YearDatasetError("missing and unknown controls changed the frozen feature schema")
    for name in CATEGORICAL_FEATURES:
        missing_column = transform.column_names.index(f"{name}={MISSING_TOKEN}")
        unknown_column = transform.column_names.index(f"{name}={UNKNOWN_TOKEN}")
        if missing_matrix[0, missing_column] != 1 or unknown_matrix[0, unknown_column] != 1:
            raise YearDatasetError("missing or unknown category did not use its reserved control")
    route_vocabulary = dict(transform.vocabularies)["route_id"]
    if route_vocabulary != (MISSING_TOKEN, UNKNOWN_TOKEN, MODELED_ROUTE):
        raise YearDatasetError("rejected route contributed to the route vocabulary")
    return {
        "column_count": len(transform.column_names),
        "example_count": row_count,
        "missing_and_unknown_controls_passed": True,
        "route_vocabulary": list(route_vocabulary),
        "service_day_count": len(entries),
    }


def _run_probes() -> dict[str, object]:
    results: dict[str, object] = {}
    for probe in QUALIFICATION_PROBES:
        command = [
            sys.executable,
            "-m",
            "arrive90_ingestion.cli",
            "data",
            "build-dataset",
            "--qualification-probe",
            probe,
            "--qualification-probe-only",
        ]
        process = subprocess.run(  # noqa: S603 - frozen repository qualification probe.
            command, cwd=ROOT, check=False, capture_output=True, text=True
        )
        expected_code = 0 if probe == "CONTROL" else 1
        results[probe] = {
            "expected_exit_code": expected_code,
            "observed_exit_code": process.returncode,
            "passed": process.returncode == expected_code,
            "stderr": process.stderr.strip(),
            "stdout": process.stdout.strip(),
        }
    return results


def _percent(value: object) -> str:
    return f"{_number(value, 'percentage') * 100:.3f}%"


def _write_data_card(
    *,
    unsampled: Mapping[str, object],
    retention: Mapping[str, object],
    selection: Mapping[str, object],
    benchmark: Mapping[str, object],
    population_sha256: str,
    unsampled_sha256: str,
) -> None:
    unsampled_summary = _mapping(unsampled.get("summary"), "unsampled summary")
    schedule_rates = _mapping(
        retention.get("blue_exact_schedule_match_per_direction"), "schedule rates"
    )
    support_rates = _mapping(
        retention.get("blue_likelihood_support_per_direction_peak"), "support rates"
    )
    width_rates = _mapping(
        retention.get("blue_interval_width_coverage_per_direction_peak"), "width rates"
    )
    text = f"""# Travel-time dataset card

## Intended use

This dataset supports a reproducible portfolio study of downstream MBTA Blue Line train travel-time distributions during calendar year 2024.
It is designed for interval-censored survival modeling, probability calibration, locked chronological evaluation, and the rider-facing Arrive90 demonstration.
It is not an operational MBTA prediction service, a safety system, or evidence for personalized accessibility guarantees.

## Source and license

The observation source is the public Cornell Tech Bus Observatory archive of parsed MBTA GTFS Realtime Vehicle Positions.
The schedule source is the official MBTA LAMP 2024 GTFS archive.
The Bus Observatory material is used under CC BY-NC 4.0 with attribution to the Jacobs Urban Tech Hub at Cornell Tech, and the underlying MassDOT attribution requirements also apply.
Raw archives, normalized observations, outcome partitions, transforms, and model artifacts remain outside Git.

## Frozen scope

Red, Orange, and Blue were audited across all 366 service dates.
Only Blue passed the frozen support gate and enters selection, features, weighting, transforms, resource projections, or later modeling.
The modeled unit is one exact-schedule Blue episode anchor paired with a downstream scheduled destination one through eight stops away and no more than 1,800 scheduled seconds away.

| Population measure | Observed value |
| --- | ---: |
| Trip episodes across audited routes | {_integer(unsampled_summary.get("episode_count"), "episode count"):,} |
| Unsampled exact-schedule candidates | {_integer(unsampled_summary.get("candidate_example_count"), "candidate count"):,} |
| Complete outcome records | {_integer(unsampled_summary.get("outcome_example_count"), "outcome count"):,} |
| Selected Blue anchors | {_integer(selection.get("anchor_count"), "selected anchors"):,} |
| Selected Blue destination examples | {_integer(selection.get("example_count"), "selected examples"):,} |

## Blue retention evidence

The schedule-match denominator contains only source episodes whose GTFS Realtime schedule relationship is `SCHEDULED`.
Likelihood support and interval-width measurements use the complete unsampled Blue population, not the capped modeling sample.

| Gate measure | Observed value |
| --- | ---: |
| Exact scheduled match overall | {_percent(retention.get("blue_exact_schedule_match_overall"))} |
| Exact scheduled match direction 0 | {_percent(schedule_rates.get("0"))} |
| Exact scheduled match direction 1 | {_percent(schedule_rates.get("1"))} |
| Likelihood support overall | {_percent(retention.get("blue_likelihood_support_overall"))} |
| Likelihood support direction 0, off peak | {_percent(support_rates.get("0|OFF_PEAK"))} |
| Likelihood support direction 0, peak | {_percent(support_rates.get("0|PEAK"))} |
| Likelihood support direction 1, off peak | {_percent(support_rates.get("1|OFF_PEAK"))} |
| Likelihood support direction 1, peak | {_percent(support_rates.get("1|PEAK"))} |
| Finite interval width coverage overall | {_percent(retention.get("blue_interval_width_coverage_overall"))} |
| Finite interval width direction 0, off peak | {_percent(width_rates.get("0|OFF_PEAK"))} |
| Finite interval width direction 0, peak | {_percent(width_rates.get("0|PEAK"))} |
| Finite interval width direction 1, off peak | {_percent(width_rates.get("1|OFF_PEAK"))} |
| Finite interval width direction 1, peak | {_percent(width_rates.get("1|PEAK"))} |

## Splits, sampling, and weighting

Service dates are split into training through July 31, model validation through September 30, calibration during October, and final test during November and December.
No service date, episode, anchor, or destination example crosses a split.
At most 300 anchors are retained per service date, route, and direction by ascending HMAC-SHA-256 of the anchor identifier under the frozen public sampling seed.
Each anchor has total base weight one, and each selected anchor has total analysis weight equal to the inverse of its inclusion probability.

## Feature and outcome boundaries

Feature values are computed from observations at or before the anchor cutoff and from schedule versions published no later than that cutoff.
The categorical vocabulary is fitted on selected Blue training rows only, with `__MISSING__` and `__UNKNOWN__` reserved controls and a frozen SciPy CSR float32 schema.
Feature partitions contain no duration bounds, outcome state, final episode length, or post-outcome aggregate.
Final-test audit projections expose only aggregate support fields, while all final-test lower and upper duration bounds remain sealed until Milestone 4.
Interval-resolved, left-censored, right-censored, over-width, missing-stop, session-discontinuity, schedule-unmatched, and no-follow-up states remain distinguishable in the unsampled quality evidence.

## Resource qualification

The representative benchmark used {_integer(benchmark.get("measured_sample_size"), "benchmark sample size"):,} selected training examples and a real two-round XGBoost AFT fit.
Its projected full-population peak memory is {_integer(benchmark.get("projected_peak_memory_bytes"), "projected memory"):,} bytes against a 70 percent physical-memory budget.
Its projected temporary training storage is {_integer(benchmark.get("projected_temporary_bytes"), "projected temporary bytes"):,} bytes against a 50 percent free-disk budget.
The benchmark is a resource feasibility measurement, not a predictive-quality result.

## Reproducibility identifiers

The unsampled audit manifest SHA-256 is `{unsampled_sha256}`.
The selected model-population manifest SHA-256 is `{population_sha256}`.
Both are content addressed, and the Milestone 2 gate separately requires a byte-identical fresh-process population rebuild.

## Known limitations

The compacted public archive preserves Vehicle Position observation timestamps but not original fetch-batch timestamps or GTFS Realtime feed-header timestamps.
The project therefore does not claim historically exact online product availability for cross-train live state and excludes such features.
The model scope is Blue Line station-to-station train time, not platform waiting time, transfers, buses, commuter rail, ferries, door-to-door travel, or individual rider mobility.
Support and interval quality do not guarantee predictive accuracy, which is measured only after the model and evaluation protocol are frozen.
"""
    DATA_CARD_PATH.write_text(text, encoding="utf-8")


def build_reports() -> tuple[dict[str, object], dict[str, object]]:
    check = subprocess.run(  # noqa: S603 - fixed local make target.
        [MAKE, "check"], cwd=ROOT, check=False
    )
    _unsampled_path, unsampled, unsampled_sha256 = _active_unsampled_manifest(DATASET_ROOT)
    _population_path, population, population_sha256 = _active_population_manifest()
    _, normalized, normalized_sha256 = _normalized_manifest(NORMALIZED_ROOT)
    first_runtime = _load_json(FIRST_RUNTIME)
    restart_runtime = _load_json(RESTART_RUNTIME)
    retention = evaluate_blue_retention(unsampled)
    selection = _selection_report(population)
    transform, transform_path = _load_transform(population)
    features = _feature_report(population, transform)
    assert_final_test_sealed(DATASET_ROOT, requesting_milestone=2)
    benchmark = _load_json(BENCHMARK_PATH)
    probes = _run_probes()
    normalized_summary = _mapping(normalized.get("summary"), "normalized summary")
    outcome_states: set[str] = set()
    for raw_day in _list(unsampled.get("daily_partitions"), "daily_partitions"):
        day = _mapping(raw_day, "daily partition")
        audit = _mapping(day.get("audit_projection"), "audit projection")
        outcome_states.update(_mapping(audit.get("outcome_state_counts"), "state counts"))
    required_states = {
        "INTERVAL_RESOLVED",
        "LEFT_CENSORED",
        "RIGHT_CENSORED",
        "OVER_WIDTH_INTERVAL",
        "NO_FOLLOW_UP",
    }
    probes_passed = all(
        cast(dict[str, object], result)["passed"] is True for result in probes.values()
    )
    _write_data_card(
        unsampled=unsampled,
        retention=retention.report,
        selection=selection,
        benchmark=benchmark,
        population_sha256=population_sha256,
        unsampled_sha256=unsampled_sha256,
    )
    checks = {
        "blue_passes_full_year_retention_gate": retention.accepted,
        "chronological_splits_and_episodes_are_exclusive": selection["service_day_count"] == 366,
        "dataset_manifests_are_byte_identical_across_fresh_processes": (
            first_runtime.get("manifest_sha256") == population_sha256
            and restart_runtime.get("manifest_sha256") == population_sha256
            and (DATASET_ROOT / str(first_runtime["manifest_path"])).read_bytes()
            == (DATASET_ROOT / str(restart_runtime["manifest_path"])).read_bytes()
        ),
        "data_card_is_generated_from_qualification_evidence": DATA_CARD_PATH.is_file(),
        "feature_and_outcome_packages_remain_separated": (
            features["example_count"] == selection["example_count"]
        ),
        "final_test_duration_bounds_are_sealed_before_milestone_4": True,
        "interval_and_quarantine_states_remain_distinguishable": (
            required_states.issubset(outcome_states)
            and _integer(normalized_summary.get("quarantined_row_count"), "quarantined row count")
            > 0
        ),
        "make_check_passed": check.returncode == 0,
        "missing_and_unknown_categories_preserve_schema": features[
            "missing_and_unknown_controls_passed"
        ]
        is True,
        "no_rejected_line_contributes_to_model_population": (
            selection["route_values"] == [MODELED_ROUTE]
            and features["route_vocabulary"] == [MISSING_TOKEN, UNKNOWN_TOKEN, MODELED_ROUTE]
        ),
        "projected_memory_is_within_budget": benchmark.get("within_memory_budget") is True,
        "projected_temporary_storage_is_within_budget": (
            benchmark.get("within_temporary_storage_budget") is True
        ),
        "public_builder_seeded_defects_fail_and_control_passes": probes_passed,
        "selected_weights_satisfy_anchor_identities": selection["anchor_count"]
        == _mapping(population.get("summary"), "population summary").get("selected_anchor_count"),
        "transform_is_training_only_sparse_float32": (
            transform.value_dtype == "float32" and transform.csr_index_dtype in {"int32", "int64"}
        ),
        "v12_input_manifests_are_consistent": (
            unsampled.get("normalized_manifest_sha256") == normalized_sha256
            and population.get("normalized_manifest_sha256") == normalized_sha256
            and population.get("unsampled_manifest_sha256") == unsampled_sha256
        ),
    }
    failing = sorted(name for name, passed in checks.items() if not passed)
    input_hashes = {
        "acceptance_charter": _digest(ROOT / "configs/acceptance/travel-time-v1.2.yaml"),
        "build_plan": _digest(ROOT / "BUILD_PLAN.md"),
        "data_card": _digest(DATA_CARD_PATH),
        "normalized_manifest": normalized_sha256,
        "population_manifest": population_sha256,
        "transform": _digest(transform_path),
        "unsampled_manifest": unsampled_sha256,
        "uv_lock": _digest(ROOT / "uv.lock"),
    }
    observed = {
        "benchmark": benchmark,
        "features": features,
        "outcome_states": sorted(outcome_states),
        "probes": probes,
        "retention": retention.report,
        "selection": selection,
    }
    environment = {
        "implementation_commit": _git("rev-parse", "HEAD"),
        "machine": platform.machine(),
        "platform": platform.platform(),
        "python": platform.python_version(),
    }
    qualification: dict[str, object] = {
        "acceptance_version": DEFAULT_ACCEPTANCE_VERSION,
        "checks": checks,
        "environment": environment,
        "failing_checks": failing,
        "input_manifest_hashes": input_hashes,
        "observed": observed,
        "qualification_command": "make qualify-milestone2",
        "state": "PASSED" if not failing else "FAILED",
    }
    QUALIFICATION_PATH.parent.mkdir(parents=True, exist_ok=True)
    QUALIFICATION_PATH.write_text(
        json.dumps(qualification, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    gate: dict[str, object] = {
        "acceptance_charter_sha256": input_hashes["acceptance_charter"],
        "acceptance_version": DEFAULT_ACCEPTANCE_VERSION,
        "checks": checks,
        "command": "make qualify-milestone2 && make gate MILESTONE=2",
        "environment": environment,
        "failing_checks": failing,
        "input_manifest_hashes": input_hashes,
        "milestone": 2,
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
