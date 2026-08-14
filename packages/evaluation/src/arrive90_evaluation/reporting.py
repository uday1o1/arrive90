"""Deterministic assembly of complete-population offline evaluation reports."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from arrive90_evaluation.bootstrap import bootstrap_policy_bounds
from arrive90_evaluation.freeze import FinalTestAccess, FrozenCellResult, FrozenProtocol
from arrive90_evaluation.gates import (
    PerformanceEvidence,
    PrimaryGateEvidence,
    evaluate_primary_gate,
)
from arrive90_evaluation.hypotheses import Hypothesis, ParetoPoint, holm_correction, pareto_frontier
from arrive90_evaluation.metrics import (
    PolicyPairRow,
    PredictionRow,
    QuantileRow,
    calibration_by_deadline_band,
    policy_outcome_bounds,
    policy_pair_summary,
    quantile_summary,
    resolution_rates_by_slice,
)
from arrive90_evaluation.predictive import ResolvedProbabilityRow, probability_metrics
from arrive90_evaluation.promotion import ReleaseMode, choose_release_mode


@dataclass(frozen=True)
class EvaluationInputs:
    protocol: FrozenProtocol
    access: FinalTestAccess
    prediction_rows: tuple[PredictionRow, ...]
    primary_policy_rows: tuple[PolicyPairRow, ...]
    recovery_policy_rows: tuple[PolicyPairRow, ...]
    quantile_rows: tuple[QuantileRow, ...]
    final_cell_results: tuple[FrozenCellResult, ...]
    secondary_hypotheses: tuple[Hypothesis, ...]
    performance: PerformanceEvidence
    empirical_primary_evidence: bool
    model_free_bundle_passed: bool
    bootstrap_seed: int
    bootstrap_replicates: int = 2_000


def _slice_summaries(rows: tuple[PolicyPairRow, ...]) -> dict[str, dict[str, Any]]:
    identifiers = sorted({value for row in rows for value in row.slice_ids}, key=str.encode)
    return {
        identifier: asdict(
            policy_pair_summary(tuple(row for row in rows if identifier in row.slice_ids))
        )
        for identifier in identifiers
    }


def _quantile_summaries(rows: tuple[QuantileRow, ...]) -> dict[str, dict[str, Any]]:
    levels = sorted({row.quantile_level for row in rows})
    return {
        format(level, ".6f"): asdict(
            quantile_summary(tuple(row for row in rows if row.quantile_level == level))
        )
        for level in levels
    }


def build_evaluation_report(inputs: EvaluationInputs) -> dict[str, Any]:
    """Build a report only when final-test access is bound to the frozen protocol."""

    if inputs.access.protocol_hash != inputs.protocol.protocol_hash:
        raise ValueError("final-test access does not match the frozen protocol")
    if not inputs.prediction_rows or not inputs.primary_policy_rows:
        raise ValueError("evaluation requires prediction and primary-policy populations")
    resolved = tuple(
        ResolvedProbabilityRow(row.unrounded_probability, row.success, row.weight)
        for row in inputs.prediction_rows
        if row.success is not None
    )
    primary = policy_pair_summary(inputs.primary_policy_rows)
    primary_bootstrap = bootstrap_policy_bounds(
        inputs.primary_policy_rows,
        replicates=inputs.bootstrap_replicates,
        seed=inputs.bootstrap_seed,
    )
    rates = resolution_rates_by_slice(inputs.primary_policy_rows)
    gate = evaluate_primary_gate(
        PrimaryGateEvidence(
            empirical_primary_evidence=inputs.empirical_primary_evidence,
            primary_difference_lower_ci=primary_bootstrap.difference_lower.lower_95,
            pair_resolution_rate=rates["OVERALL"],
            slice_pair_resolution_rates=tuple(
                (identifier, rates[identifier])
                for identifier in sorted(rates, key=str.encode)
                if identifier != "OVERALL"
            ),
            mean_added_planned_time_seconds=primary.mean_added_planned_time_seconds or 0,
            maximum_added_planned_time_seconds=primary.maximum_added_planned_time_seconds or 0,
            added_time_population_available=primary.mean_added_planned_time_seconds is not None,
            performance=inputs.performance,
            frozen_cells=inputs.final_cell_results,
        )
    )
    learned_passed = gate.passed
    release_mode = choose_release_mode(
        learned_passed=learned_passed,
        model_free_passed=inputs.model_free_bundle_passed,
    )
    negative_results = list(gate.failing_checks)
    if not resolved:
        negative_results.append("NO_RESOLVED_ROWS_FOR_PREDICTIVE_DIAGNOSTICS")
    recovery: dict[str, Any] | None = None
    if inputs.recovery_policy_rows:
        recovery_summary = policy_pair_summary(inputs.recovery_policy_rows)
        recovery = {
            "comparison": asdict(recovery_summary),
            "population": "FROZEN_FIRST_RECOVERY_TRIGGER_ONLY",
            "recovery_model_outputs": "ALWAYS_NULL",
        }
    calibration = {
        band_id: asdict(summary)
        for band_id, summary in calibration_by_deadline_band(inputs.prediction_rows)
    }
    arrive90_outcome = policy_outcome_bounds(inputs.primary_policy_rows, policy="arrive90")
    comparator_outcome = policy_outcome_bounds(inputs.primary_policy_rows, policy="comparator")
    frontier = pareto_frontier(
        (
            ParetoPoint(
                "STATIC_FASTEST",
                comparator_outcome.success_lower,
                comparator_outcome.success_upper,
                0,
            ),
            ParetoPoint(
                "ARRIVE90_0_90_CAP_20",
                arrive90_outcome.success_lower,
                arrive90_outcome.success_upper,
                primary.mean_added_planned_time_seconds or 0,
                True,
            ),
        )
    )
    return {
        "acceptance_version": inputs.protocol.acceptance_version,
        "availability": {
            "primary_pair_resolution_rate": primary.pair_resolution_rate,
            "resolution_rate_by_slice": rates,
            "scheduled_decision_count": len(inputs.primary_policy_rows),
        },
        "calibration": calibration,
        "censoring_bounds": {
            "primary_difference_lower": primary.difference_lower,
            "primary_difference_upper": primary.difference_upper,
        },
        "evidence_kind": (
            "HISTORICAL_PRIMARY"
            if inputs.empirical_primary_evidence
            else "SYNTHETIC_MECHANICS_ONLY"
        ),
        "final_test_access": asdict(inputs.access),
        "frozen_cell_results": [asdict(cell) for cell in inputs.final_cell_results],
        "gate": asdict(gate),
        "hypotheses": [asdict(item) for item in holm_correction(inputs.secondary_hypotheses)],
        "negative_results": sorted(set(negative_results), key=str.encode),
        "pareto_frontier": [asdict(point) for point in frontier],
        "predictive_resolved_only": asdict(probability_metrics(resolved)) if resolved else None,
        "primary_policy": asdict(primary),
        "policy_success_bounds": {
            "arrive90": asdict(arrive90_outcome),
            "static_fastest": asdict(comparator_outcome),
        },
        "protocol_hash": inputs.protocol.protocol_hash,
        "quantiles": _quantile_summaries(inputs.quantile_rows),
        "recovery": recovery,
        "release_mode": ReleaseMode(release_mode).value,
        "subgroups": _slice_summaries(inputs.primary_policy_rows),
        "uncertainty": {
            "bootstrap": asdict(primary_bootstrap),
            "method": "PAIRED_COMPLETE_SERVICE_DAY_BLOCK_BOOTSTRAP",
            "replicates": inputs.bootstrap_replicates,
            "seed": inputs.bootstrap_seed,
        },
    }
