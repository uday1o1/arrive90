from __future__ import annotations

from datetime import date, time

import pytest
from arrive90_routing.population import (
    PopulationConfig,
    StationPair,
    generate_query_population,
    select_station_pairs,
)


def _config() -> PopulationConfig:
    return PopulationConfig(
        maximum_pairs_per_stratum=2,
        readiness_horizons_minutes=(0,),
        query_start_local=time(6),
        query_end_local=time(6),
        deadline_slacks_minutes=(5, 10),
    )


def test_query_population_is_deterministic_balanced_and_outcome_independent() -> None:
    dates = (date(2025, 1, 1), date(2025, 1, 2))
    pairs = (
        StationPair("a", "b", "Red"),
        StationPair("a", "c", "Red"),
        StationPair("a", "d", "Red"),
    )
    schedule_versions = dict.fromkeys(dates, "schedule")
    splits = {dates[0]: "train", dates[1]: "validation"}
    first = generate_query_population(
        reversed(pairs),
        reversed(dates),
        schedule_version_by_date=schedule_versions,
        split_by_date=splits,
        config=_config(),
    )
    second = generate_query_population(
        pairs,
        dates,
        schedule_version_by_date=schedule_versions,
        split_by_date=splits,
        config=_config(),
    )
    assert first == second
    assert len(first.selected_pairs) == 2
    assert len(first.base_queries) == 4
    assert len(first.deadline_variants) == 8
    assert sum(variant.variant_weight for variant in first.deadline_variants) == 4
    base_by_id = {base.query_id: base for base in first.base_queries}
    assignments: dict[str, list[tuple[str, int]]] = {"train": [], "validation": []}
    for variant in first.deadline_variants:
        split = base_by_id[variant.base_query_id].chronological_split
        assignments[split].append(
            (
                variant.assigned_reliability_target,
                variant.assigned_maximum_extra_time_minutes,
            )
        )
    assert all(len(set(values)) == 4 for values in assignments.values())


def test_population_retains_dates_and_rejects_missing_schedule_or_split() -> None:
    day = date(2025, 1, 1)
    pair = StationPair("a", "b", "Red")
    with pytest.raises(ValueError, match="schedule version"):
        generate_query_population(
            (pair,),
            (day,),
            schedule_version_by_date={},
            split_by_date={day: "train"},
            config=_config(),
        )
    with pytest.raises(ValueError, match="chronological split"):
        generate_query_population(
            (pair,),
            (day,),
            schedule_version_by_date={day: "schedule"},
            split_by_date={},
            config=_config(),
        )
    with pytest.raises(ValueError, match="must differ"):
        StationPair("same", "same", "Red")


def test_pair_selection_caps_each_stratum_and_config_fails_closed() -> None:
    config = _config()
    selected = select_station_pairs(
        (
            StationPair("a", "b", "Red"),
            StationPair("a", "c", "Red"),
            StationPair("x", "y", "Orange"),
        ),
        config,
    )
    assert sum(pair.stratum == "Red" for pair in selected) == 2
    assert sum(pair.stratum == "Orange" for pair in selected) == 1
    with pytest.raises(ValueError, match="positive"):
        PopulationConfig(maximum_pairs_per_stratum=0)
    with pytest.raises(ValueError, match="increasing"):
        PopulationConfig(query_start_local=time(7), query_end_local=time(6))
