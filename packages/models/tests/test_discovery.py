from __future__ import annotations

from collections.abc import Mapping

import pytest
from arrive90_models.discovery import DiscoveryEvaluation, discover_eligibility


def test_discovery_removes_failures_simultaneously_and_verifies_fixed_point() -> None:
    calls: list[dict[str, bool]] = []

    def worker(manifest: Mapping[str, bool]) -> DiscoveryEvaluation:
        calls.append(dict(manifest))
        failures = frozenset(cell for cell in ("b", "c") if manifest[cell])
        return DiscoveryEvaluation("decisions", "population", {}, failures)

    artifact = discover_eligibility(
        ("c", "a", "b"),
        worker,
        acceptance_charter_hash="charter",
        algorithm_hash="algorithm",
        pretest_evidence_hashes=("z", "a"),
    )
    assert artifact.cell_inventory == ("a", "b", "c")
    assert artifact.iterations[0].simultaneous_removal_set == ("b", "c")
    assert artifact.iterations[1].simultaneous_removal_set == ()
    assert artifact.final_manifest == (("a", True), ("b", False), ("c", False))
    assert calls[-1] == calls[-2]
    assert len(artifact.artifact_hash) == 64


def test_discovery_rejects_duplicate_and_unknown_cells() -> None:
    with pytest.raises(ValueError, match="nonempty and unique"):
        discover_eligibility(
            ("a", "a"),
            lambda _: DiscoveryEvaluation("d", "p", {}, frozenset()),
            acceptance_charter_hash="c",
            algorithm_hash="a",
            pretest_evidence_hashes=(),
        )
    with pytest.raises(ValueError, match="unknown cells"):
        discover_eligibility(
            ("a",),
            lambda _: DiscoveryEvaluation("d", "p", {}, frozenset({"unknown"})),
            acceptance_charter_hash="c",
            algorithm_hash="a",
            pretest_evidence_hashes=(),
        )
