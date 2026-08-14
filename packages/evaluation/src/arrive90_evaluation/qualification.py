"""Deterministic synthetic qualification fixture for offline evaluation mechanics."""

from __future__ import annotations

import json
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from arrive90_models.discovery import DiscoveryEvaluation, discover_eligibility

from arrive90_evaluation.freeze import (
    FrozenCellResult,
    FrozenProtocol,
    canonical_hash,
    open_final_test,
)
from arrive90_evaluation.gates import PerformanceEvidence
from arrive90_evaluation.hypotheses import Hypothesis
from arrive90_evaluation.metrics import PolicyPairRow, PredictionRow, QuantileRow
from arrive90_evaluation.reporting import EvaluationInputs, build_evaluation_report


def _hash(label: str) -> str:
    return canonical_hash({"synthetic_fixture": label})


def _discovery_artifact() -> tuple[str, str]:
    def worker(manifest: Mapping[str, bool]) -> DiscoveryEvaluation:
        failures: set[str] = set()
        if manifest["deadline-band-low"]:
            failures.add("deadline-band-low")
        if not manifest["deadline-band-low"] and manifest["station-slice"]:
            failures.add("station-slice")
        decisions = canonical_hash({"manifest": manifest, "surface": "decisions"})
        population = canonical_hash({"manifest": manifest, "surface": "population"})
        metrics = {
            cell: {"eligible": enabled, "fails": cell in failures}
            for cell, enabled in sorted(manifest.items(), key=lambda item: item[0].encode())
        }
        return DiscoveryEvaluation(decisions, population, metrics, frozenset(failures))

    artifact = discover_eligibility(
        ("station-slice", "deadline-band-low", "quantile-0.90"),
        worker,
        acceptance_charter_hash=_hash("acceptance-charter"),
        algorithm_hash=_hash("decision-kernel"),
        pretest_evidence_hashes=(_hash("pretest-a"), _hash("pretest-b")),
    )
    return artifact.final_manifest_hash, artifact.artifact_hash


def build_qualification_payload() -> dict[str, Any]:
    eligibility_hash, discovery_hash = _discovery_artifact()
    protocol = FrozenProtocol(
        acceptance_version="v1-synthetic-mechanics",
        frozen_at_utc="2025-01-01T00:00:00Z",
        query_manifest_hash=_hash("query-manifest"),
        candidate_manifest_hash=_hash("candidate-manifest"),
        model_bundle_hash=_hash("model-bundle"),
        calibration_hash=_hash("calibration"),
        support_manifest_hash=_hash("support-manifest"),
        eligibility_manifest_hash=eligibility_hash,
        discovery_artifact_hash=discovery_hash,
        decision_policy_hash=_hash(f"decision-policy:{eligibility_hash}:{discovery_hash}"),
        transfer_bundle_hash=_hash("transfer-classifier-and-calibrator"),
        transfer_support_hash=_hash("transfer-deciles-stations-and-trigger-cells"),
        quantile_support_hash=_hash("quantile-levels-and-support"),
        recovery_policy_hash=_hash("deterministic-recovery-policy"),
        secondary_hypothesis_hash=_hash("ordered-secondary-hypotheses"),
        evaluation_code_hash=_hash("evaluation-code"),
    )
    predictions = (
        PredictionRow("d1", "q1", "day-1", 1, Decimal("0.910000"), 0.91, True),
        PredictionRow("d2", "q2", "day-2", 1, Decimal("0.910000"), 0.91, True),
        PredictionRow("d3", "q3", "day-3", 1, Decimal("0.810000"), 0.81, None),
        PredictionRow("d4", "q4", "day-4", 1, Decimal("0.810000"), 0.81, False),
    )
    primary = (
        PolicyPairRow("v1", "q1", "day-1", 1, True, False, 120, ("ordinary",)),
        PolicyPairRow("v2", "q2", "day-2", 1, True, True, 120, ("ordinary",)),
        PolicyPairRow("v3", "q3", "day-3", 1, None, False, 300, ("disruption",)),
        PolicyPairRow("v4", "q4", "day-4", 1, False, None, 300, ("disruption",)),
    )
    recovery = (
        PolicyPairRow("r1", "q1", "day-1", 1, True, False, 60),
        PolicyPairRow("r2", "q2", "day-2", 1, True, True, 60),
    )
    quantiles = (
        QuantileRow("day-1", 1, 0.5, 600, 540, 570),
        QuantileRow("day-2", 1, 0.5, 600, 580, 630),
        QuantileRow("day-3", 1, 0.9, 900, None, None),
        QuantileRow("day-4", 1, 0.9, 900, 840, 960),
    )
    report = build_evaluation_report(
        EvaluationInputs(
            protocol=protocol,
            access=open_final_test(protocol, opened_at_utc="2025-02-01T00:00:00Z"),
            prediction_rows=predictions,
            primary_policy_rows=primary,
            recovery_policy_rows=recovery,
            quantile_rows=quantiles,
            final_cell_results=(
                FrozenCellResult("deadline-band-low", False, None),
                FrozenCellResult("quantile-0.90", True, True),
                FrozenCellResult("station-slice", False, None),
            ),
            secondary_hypotheses=(
                Hypothesis("MODEL_FREE_VS_STATIC_FASTEST", 0.20, 0),
                Hypothesis("RECOVERY_VS_CONTINUATION", 0.04, 1),
            ),
            performance=PerformanceEvidence(1.0, 1.0, 2.0),
            empirical_primary_evidence=False,
            model_free_bundle_passed=False,
            bootstrap_seed=2_904_221,
        )
    )
    return {
        "discovery_artifact_hash": discovery_hash,
        "eligibility_manifest_hash": eligibility_hash,
        "report": report,
        "status": "INSUFFICIENT_EVIDENCE",
    }


def main() -> int:
    print(json.dumps(build_qualification_payload(), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
