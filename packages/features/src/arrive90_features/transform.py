"""Deterministic training-only sparse feature transform for travel-time-v1."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy import sparse  # type: ignore[import-untyped]

from arrive90_features.travel_time_registry import (
    CATEGORICAL_FEATURES,
    NUMERIC_FEATURES,
    TRAVEL_TIME_V1_REGISTRY,
)

MISSING_TOKEN = "__MISSING__"  # noqa: S105 - categorical sentinel, not a credential.
UNKNOWN_TOKEN = "__UNKNOWN__"  # noqa: S105 - categorical sentinel, not a credential.
TRANSFORM_VERSION = "travel-time-csr-v1"
type FeatureValue = str | int | float | bool | None


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode()


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _category(value: FeatureValue, *, training: bool) -> str:
    if value is None:
        return MISSING_TOKEN
    text = str(value)
    if text in {MISSING_TOKEN, UNKNOWN_TOKEN}:
        raise ValueError("raw categorical values cannot equal reserved transform tokens")
    if not text:
        raise ValueError("categorical values cannot be empty")
    return text


def _numeric(value: FeatureValue, name: str) -> float:
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return float(value)
    if not isinstance(value, int | float):
        raise ValueError(f"numeric feature {name} must be numeric, boolean, or null")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"numeric feature {name} must be finite")
    return numeric


@dataclass(frozen=True, slots=True)
class FeatureTransformInput:
    """One immutable raw feature row supplied to a fitted transform."""

    row_id: str
    values: Mapping[str, FeatureValue]

    def __post_init__(self) -> None:
        if not self.row_id:
            raise ValueError("row_id must be nonempty")
        expected = set(TRAVEL_TIME_V1_REGISTRY.specs)
        observed = set(self.values)
        if observed != expected:
            raise ValueError(
                f"feature row schema mismatch: missing={sorted(expected - observed)}, "
                f"unknown={sorted(observed - expected)}"
            )


@dataclass(frozen=True, slots=True)
class FittedFeatureTransform:
    """Frozen training vocabulary and numeric-column order."""

    training_row_sha256: str
    vocabularies: tuple[tuple[str, tuple[str, ...]], ...]
    column_names: tuple[str, ...]
    output_schema_sha256: str
    csr_index_dtype: str
    value_dtype: str = "float32"
    version: str = TRANSFORM_VERSION

    def __post_init__(self) -> None:
        expected_names = tuple(name for name, _ in self.vocabularies)
        if expected_names != CATEGORICAL_FEATURES:
            raise ValueError("categorical vocabularies must follow registry order")
        for _, vocabulary in self.vocabularies:
            if vocabulary[:2] != (MISSING_TOKEN, UNKNOWN_TOKEN):
                raise ValueError("every vocabulary must begin with missing and unknown")
            if vocabulary[2:] != tuple(sorted(vocabulary[2:], key=str.encode)):
                raise ValueError("observed categories must be bytewise sorted")
            if len(set(vocabulary)) != len(vocabulary):
                raise ValueError("categorical vocabularies must be unique")
        if self.output_schema_sha256 != _sha256(list(self.column_names)):
            raise ValueError("output schema hash does not match the frozen column order")

    @property
    def manifest(self) -> dict[str, Any]:
        """Return the complete JSON-safe transformation manifest."""

        return {
            "categorical_vocabularies": {
                name: list(vocabulary) for name, vocabulary in self.vocabularies
            },
            "column_names": list(self.column_names),
            "csr_index_dtype": self.csr_index_dtype,
            "feature_registry_sha256": TRAVEL_TIME_V1_REGISTRY.manifest_hash,
            "missing_policy": MISSING_TOKEN,
            "output_schema_sha256": self.output_schema_sha256,
            "training_row_sha256": self.training_row_sha256,
            "unknown_policy": UNKNOWN_TOKEN,
            "value_dtype": self.value_dtype,
            "version": self.version,
        }

    def transform(self, rows: Sequence[FeatureTransformInput]) -> sparse.csr_matrix:
        """Apply the frozen transform without extending a vocabulary."""

        vocabulary_maps = {
            name: {value: index for index, value in enumerate(vocabulary)}
            for name, vocabulary in self.vocabularies
        }
        categorical_offsets: dict[str, int] = {}
        offset = len(NUMERIC_FEATURES)
        for name, vocabulary in self.vocabularies:
            categorical_offsets[name] = offset
            offset += len(vocabulary)

        row_indices: list[int] = []
        column_indices: list[int] = []
        data: list[float] = []
        seen_ids: set[str] = set()
        for row_index, row in enumerate(rows):
            if row.row_id in seen_ids:
                raise ValueError("feature transform row identifiers must be unique")
            seen_ids.add(row.row_id)
            for column_index, name in enumerate(NUMERIC_FEATURES):
                numeric = _numeric(row.values[name], name)
                if numeric != 0:
                    row_indices.append(row_index)
                    column_indices.append(column_index)
                    data.append(numeric)
            for name in CATEGORICAL_FEATURES:
                value = _category(row.values[name], training=False)
                mapping = vocabulary_maps[name]
                category_index = mapping.get(value, mapping[UNKNOWN_TOKEN])
                row_indices.append(row_index)
                column_indices.append(categorical_offsets[name] + category_index)
                data.append(1.0)

        matrix = sparse.csr_matrix(
            (
                np.asarray(data, dtype=np.float32),
                (
                    np.asarray(row_indices, dtype=np.int64),
                    np.asarray(column_indices, dtype=np.int64),
                ),
            ),
            shape=(len(rows), len(self.column_names)),
            dtype=np.float32,
        )
        matrix.sum_duplicates()
        matrix.sort_indices()
        return matrix


def fit_feature_transform(rows: Sequence[FeatureTransformInput]) -> FittedFeatureTransform:
    """Fit deterministic vocabularies from selected retained training rows only."""

    if not rows:
        raise ValueError("training feature rows cannot be empty")
    if len({row.row_id for row in rows}) != len(rows):
        raise ValueError("training feature row identifiers must be unique")
    vocabularies: list[tuple[str, tuple[str, ...]]] = []
    for name in CATEGORICAL_FEATURES:
        observed = {
            _category(row.values[name], training=True)
            for row in rows
            if row.values[name] is not None
        }
        vocabulary = (
            MISSING_TOKEN,
            UNKNOWN_TOKEN,
            *sorted(observed, key=str.encode),
        )
        vocabularies.append((name, vocabulary))
    column_names = list(NUMERIC_FEATURES)
    for name, vocabulary in vocabularies:
        column_names.extend(f"{name}={value}" for value in vocabulary)
    training_payload = [
        {
            "row_id": row.row_id,
            "values": {name: row.values[name] for name in TRAVEL_TIME_V1_REGISTRY.specs},
        }
        for row in rows
    ]
    provisional = FittedFeatureTransform(
        training_row_sha256=_sha256(training_payload),
        vocabularies=tuple(vocabularies),
        column_names=tuple(column_names),
        output_schema_sha256=_sha256(column_names),
        csr_index_dtype="PENDING",
    )
    training_matrix = provisional.transform(rows)
    return FittedFeatureTransform(
        training_row_sha256=provisional.training_row_sha256,
        vocabularies=provisional.vocabularies,
        column_names=provisional.column_names,
        output_schema_sha256=provisional.output_schema_sha256,
        csr_index_dtype=str(training_matrix.indices.dtype),
    )
