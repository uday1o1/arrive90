from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml
from arrive90_data_contracts.gates import DEFAULT_ACCEPTANCE_VERSION

ROOT = Path(__file__).parents[3]
ACCEPTANCE_VERSION = "travel-time-v1.2"


def _yaml(path: Path) -> dict[str, object]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _json(path: Path) -> dict[str, object]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_active_acceptance_charter_binds_exact_build_plan() -> None:
    build_plan = ROOT / "BUILD_PLAN.md"
    build_plan_sha256 = hashlib.sha256(build_plan.read_bytes()).hexdigest()
    charter = _yaml(ROOT / "configs/acceptance/travel-time-v1.2.yaml")
    tracker = _json(ROOT / "configs/acceptance/travel-time-v1.2-milestones.json")

    assert DEFAULT_ACCEPTANCE_VERSION == ACCEPTANCE_VERSION
    assert charter["acceptance_version"] == ACCEPTANCE_VERSION
    assert charter["artifact_family"] == "travel-time-v1"
    assert charter["build_plan_sha256"] == build_plan_sha256
    assert tracker["acceptance_version"] == ACCEPTANCE_VERSION
    assert tracker["build_plan_sha256"] == build_plan_sha256


def test_acceptance_envelope_is_consistent_across_source_locks() -> None:
    paths = (
        ROOT / "configs/sources/bus-observatory-mbta-2024.yaml",
        ROOT / "configs/sources/mbta-gtfs-archive-2024.yaml",
        ROOT / "configs/source-locks/mbta-2024.json",
        ROOT / "configs/source-locks/mbta-2024-acquired.json",
        ROOT / "configs/source-locks/milestone0-acquired.json",
    )
    for path in paths:
        loaded = _yaml(path) if path.suffix == ".yaml" else _json(path)
        assert loaded["acceptance_version"] == ACCEPTANCE_VERSION, path


def test_trackable_episode_gate_is_pre_outcome_and_explicit() -> None:
    charter = _yaml(ROOT / "configs/acceptance/travel-time-v1.2.yaml")
    one_day_gate = charter["one_day_gate"]
    assert isinstance(one_day_gate, dict)
    assert one_day_gate["trackable_episode_min_distinct_canonical_event_timestamps"] == 2
    assert one_day_gate["trackable_multi_stop_episode_rate_per_line_min"] == 0.70
    assert one_day_gate["trackable_multi_stop_min_distinct_unambiguous_stopped_sequences"] == 2
    required_reporting = one_day_gate["required_episode_reporting"]
    assert isinstance(required_reporting, list)
    assert "unconditioned_multi_stop_episode_rate" in required_reporting
    assert "trackable_multi_stop_episode_rate" in required_reporting
    assert "post_gap_fragment_count" in required_reporting


def test_v12_scope_and_requalification_order_are_explicit() -> None:
    charter = _yaml(ROOT / "configs/acceptance/travel-time-v1.2.yaml")
    tracker = _json(ROOT / "configs/acceptance/travel-time-v1.2-milestones.json")
    scope = charter["scope"]
    assert isinstance(scope, dict)
    assert scope["audited_route_ids"] == ["Red", "Orange", "Blue"]
    assert scope["modeled_route_ids"] == ["Blue"]
    assert scope["required_retained_line_count"] == 1
    full_year_gate = charter["full_year_gate"]
    assert isinstance(full_year_gate, dict)
    assert full_year_gate["likelihood_support_blue_overall_min"] == 0.75
    assert full_year_gate["likelihood_support_blue_direction_peak_slice_min"] == 0.70
    milestones = tracker["milestones"]
    assert isinstance(milestones, list)
    states = {item["milestone"]: item["state"] for item in milestones if isinstance(item, dict)}
    assert list(states) == list(range(8))
    if states[2] in {"IN_PROGRESS", "ACCEPTED"}:
        assert states[0] == states[1] == "ACCEPTED"
