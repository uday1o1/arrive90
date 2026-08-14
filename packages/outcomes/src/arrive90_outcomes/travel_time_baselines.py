"""Frozen point baselines for the 2024 Blue Line travel-time study."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

BACKOFF_ORDER = (
    "FULL_CELL",
    "LINE_DIRECTION_ORIGIN_DESTINATION",
    "LINE_DIRECTION_DESTINATION_OFFSET",
    "GLOBAL_DESTINATION_OFFSET",
)


def official_scheduled_remaining_seconds(value: float) -> float:
    """Return the official schedule point diagnostic after contract validation."""

    if value <= 0:
        raise ValueError("scheduled remaining travel time must be positive")
    return float(value)


def three_hour_bucket(local_hour: int) -> str:
    """Return the frozen three-hour local-time bucket."""

    if not 0 <= local_hour <= 23:
        raise ValueError("local hour must be inside zero through 23")
    start = local_hour - local_hour % 3
    return f"{start:02d}:00-{start + 3:02d}:00"


@dataclass(frozen=True, slots=True)
class EmpiricalMidpointQuery:
    """Outcome-free lookup fields for the empirical diagnostic."""

    anchor_id: str
    route_id: str
    direction_id: str
    origin_stop_id: str
    destination_stop_id: str
    destination_offset: int
    day_type: str
    time_bucket: str

    def __post_init__(self) -> None:
        if not self.anchor_id:
            raise ValueError("empirical baseline anchor identifier must be nonempty")
        if not 1 <= self.destination_offset <= 8:
            raise ValueError("empirical destination offset must be inside one through eight")
        if self.day_type not in {"WEEKDAY", "WEEKEND"}:
            raise ValueError("empirical day type is invalid")

    def backoff_keys(self) -> tuple[tuple[str, ...], ...]:
        return (
            (
                "FULL_CELL",
                self.route_id,
                self.direction_id,
                self.origin_stop_id,
                self.destination_stop_id,
                self.day_type,
                self.time_bucket,
            ),
            (
                "LINE_DIRECTION_ORIGIN_DESTINATION",
                self.route_id,
                self.direction_id,
                self.origin_stop_id,
                self.destination_stop_id,
            ),
            (
                "LINE_DIRECTION_DESTINATION_OFFSET",
                self.route_id,
                self.direction_id,
                str(self.destination_offset),
            ),
            ("GLOBAL_DESTINATION_OFFSET", str(self.destination_offset)),
        )


@dataclass(frozen=True, slots=True)
class EmpiricalMidpointRow(EmpiricalMidpointQuery):
    """One finite training example eligible for the empirical diagnostic."""

    example_id: str
    midpoint_seconds: float
    analysis_weight: float

    def __post_init__(self) -> None:
        EmpiricalMidpointQuery.__post_init__(self)
        if not self.example_id:
            raise ValueError("empirical baseline example identifier must be nonempty")
        if self.midpoint_seconds <= 0 or self.analysis_weight <= 0:
            raise ValueError("empirical midpoint and weight must be positive")


@dataclass(frozen=True, slots=True)
class EmpiricalPrediction:
    seconds: float | None
    backoff_level: str | None


@dataclass(frozen=True, slots=True)
class EmpiricalMidpointBaseline:
    """Training-only weighted-median cells under the frozen support backoff."""

    cells: tuple[tuple[tuple[str, ...], float, int, int], ...]
    minimum_finite_examples: int = 100
    minimum_distinct_anchors: int = 25

    def __post_init__(self) -> None:
        keys = tuple(key for key, _, _, _ in self.cells)
        if keys != tuple(sorted(keys, key=lambda item: json.dumps(item).encode())):
            raise ValueError("empirical baseline cells must use canonical bytewise order")
        if len(set(keys)) != len(keys):
            raise ValueError("empirical baseline cells must be unique")

    @property
    def manifest(self) -> dict[str, Any]:
        return {
            "backoff_order": list(BACKOFF_ORDER),
            "cells": [
                {
                    "distinct_anchor_count": anchor_count,
                    "finite_example_count": example_count,
                    "key": list(key),
                    "weighted_median_seconds": median,
                }
                for key, median, example_count, anchor_count in self.cells
            ],
            "minimum_distinct_anchors": self.minimum_distinct_anchors,
            "minimum_finite_examples": self.minimum_finite_examples,
            "version": "training-empirical-midpoint-v1",
        }

    @property
    def manifest_sha256(self) -> str:
        body = json.dumps(self.manifest, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(body).hexdigest()

    @classmethod
    def from_manifest(cls, manifest: dict[str, Any]) -> EmpiricalMidpointBaseline:
        if manifest.get("version") != "training-empirical-midpoint-v1":
            raise ValueError("empirical baseline manifest version is invalid")
        raw_cells = manifest.get("cells")
        if not isinstance(raw_cells, list):
            raise ValueError("empirical baseline cells must be a list")
        cells: list[tuple[tuple[str, ...], float, int, int]] = []
        for raw in raw_cells:
            if not isinstance(raw, dict) or not isinstance(raw.get("key"), list):
                raise ValueError("empirical baseline cell is invalid")
            cells.append(
                (
                    tuple(str(value) for value in raw["key"]),
                    float(raw["weighted_median_seconds"]),
                    int(raw["finite_example_count"]),
                    int(raw["distinct_anchor_count"]),
                )
            )
        return cls(
            tuple(cells),
            minimum_finite_examples=int(manifest["minimum_finite_examples"]),
            minimum_distinct_anchors=int(manifest["minimum_distinct_anchors"]),
        )

    def predict(self, row: EmpiricalMidpointQuery) -> EmpiricalPrediction:
        cells = {key: median for key, median, _, _ in self.cells}
        for key in row.backoff_keys():
            if key in cells:
                return EmpiricalPrediction(cells[key], key[0])
        return EmpiricalPrediction(None, None)


def _weighted_median(values: list[tuple[float, float, str]]) -> float:
    ordered = sorted(values, key=lambda item: (item[0], item[2].encode()))
    midpoint = sum(weight for _, weight, _ in ordered) / 2.0
    cumulative = 0.0
    for value, weight, _ in ordered:
        cumulative += weight
        if cumulative >= midpoint:
            return value
    raise ValueError("weighted median received no positive mass")


def fit_empirical_midpoint_baseline(
    rows: tuple[EmpiricalMidpointRow, ...],
    *,
    minimum_finite_examples: int = 100,
    minimum_distinct_anchors: int = 25,
) -> EmpiricalMidpointBaseline:
    """Fit supported cells from finite training rows and no other split."""

    if not rows or minimum_finite_examples <= 0 or minimum_distinct_anchors <= 0:
        raise ValueError("empirical baseline fit configuration is invalid")
    if len({row.example_id for row in rows}) != len(rows):
        raise ValueError("empirical training examples must be unique")
    grouped: dict[tuple[str, ...], list[EmpiricalMidpointRow]] = defaultdict(list)
    for row in rows:
        for key in row.backoff_keys():
            grouped[key].append(row)
    supported: list[tuple[tuple[str, ...], float, int, int]] = []
    for key, members in grouped.items():
        anchor_count = len({member.anchor_id for member in members})
        if len(members) < minimum_finite_examples or anchor_count < minimum_distinct_anchors:
            continue
        median = _weighted_median(
            [
                (member.midpoint_seconds, member.analysis_weight, member.example_id)
                for member in members
            ]
        )
        supported.append((key, median, len(members), anchor_count))
    supported.sort(key=lambda item: json.dumps(item[0]).encode())
    return EmpiricalMidpointBaseline(
        tuple(supported),
        minimum_finite_examples=minimum_finite_examples,
        minimum_distinct_anchors=minimum_distinct_anchors,
    )
