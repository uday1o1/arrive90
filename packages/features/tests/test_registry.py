from __future__ import annotations

from pathlib import Path

import pytest
from arrive90_features.registry import (
    FeatureRegistry,
    FeatureSource,
    FeatureSpec,
)
from arrive90_features.travel_time_registry import TRAVEL_TIME_V1_REGISTRY


def _spec(name: str, source: FeatureSource) -> FeatureSpec:
    return FeatureSpec(
        name, "owner", "float", None, source, "event", "availability", "derive", "none", "fixture"
    )


def test_travel_time_registry_is_stable_and_contains_no_predictions_or_outcomes() -> None:
    assert len(TRAVEL_TIME_V1_REGISTRY.manifest_hash) == 64
    assert all(
        spec.source not in {FeatureSource.TRIP_UPDATE_PREDICTION, FeatureSource.OUTCOME}
        for spec in TRAVEL_TIME_V1_REGISTRY.specs.values()
    )
    with pytest.raises(ValueError, match="forbidden sources"):
        FeatureRegistry("bad", (_spec("prediction", FeatureSource.TRIP_UPDATE_PREDICTION),))
    with pytest.raises(ValueError, match="duplicate"):
        FeatureRegistry(
            "bad",
            (_spec("same", FeatureSource.STATIC_SCHEDULE), _spec("same", FeatureSource.ALERT)),
        )
    with pytest.raises(ValueError, match="not registered"):
        TRAVEL_TIME_V1_REGISTRY.require("deadline_slack")


def test_online_feature_package_cannot_import_outcome_package() -> None:
    root = Path(__file__).resolve().parents[1] / "src/arrive90_features"
    assert "arrive90_outcomes" not in "\n".join(
        path.read_text(encoding="utf-8") for path in root.glob("*.py")
    )
