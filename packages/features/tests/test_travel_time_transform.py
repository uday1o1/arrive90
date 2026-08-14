from __future__ import annotations

import numpy as np
import pytest
from arrive90_features.transform import (
    MISSING_TOKEN,
    UNKNOWN_TOKEN,
    FeatureTransformInput,
    fit_feature_transform,
)
from arrive90_features.travel_time_registry import TRAVEL_TIME_V1_REGISTRY


def _row(row_id: str, **changes: object) -> FeatureTransformInput:
    values: dict[str, str | int | float | bool | None] = {}
    for name, spec in TRAVEL_TIME_V1_REGISTRY.specs.items():
        if spec.value_type == "categorical":
            values[name] = f"{name}-training"
        elif spec.value_type == "boolean":
            values[name] = False
        else:
            values[name] = 1.0
    values.update(changes)  # type: ignore[arg-type]
    return FeatureTransformInput(row_id=row_id, values=values)


def test_training_only_transform_freezes_unknown_missing_and_column_schema() -> None:
    training = (
        _row("a", route_id="Red", origin_stop_id=None),
        _row("b", route_id="Blue", origin_stop_id="place-a"),
    )
    fitted = fit_feature_transform(training)
    validation = (
        _row("validation", route_id="Orange", origin_stop_id=None),
        _row("control", route_id="Red", origin_stop_id="place-new"),
    )
    matrix = fitted.transform(validation)
    assert matrix.dtype == np.float32
    assert matrix.format == "csr"
    assert matrix.has_canonical_format
    assert matrix.shape == (2, len(fitted.column_names))
    assert fitted.csr_index_dtype == str(matrix.indices.dtype)
    assert fitted.column_names == fit_feature_transform(training).column_names
    route_unknown = fitted.column_names.index(f"route_id={UNKNOWN_TOKEN}")
    origin_missing = fitted.column_names.index(f"origin_stop_id={MISSING_TOKEN}")
    origin_unknown = fitted.column_names.index(f"origin_stop_id={UNKNOWN_TOKEN}")
    dense = matrix.toarray()
    assert dense[0, route_unknown] == 1
    assert dense[0, origin_missing] == 1
    assert dense[1, origin_unknown] == 1


def test_transform_rejects_reserved_raw_categories_and_schema_drift() -> None:
    with pytest.raises(ValueError, match="reserved"):
        fit_feature_transform((_row("a", route_id=UNKNOWN_TOKEN),))
    with pytest.raises(ValueError, match="schema mismatch"):
        FeatureTransformInput(row_id="bad", values={"route_id": "Red"})
    fitted = fit_feature_transform((_row("a"),))
    with pytest.raises(ValueError, match="unique"):
        fitted.transform((_row("same"), _row("same")))
