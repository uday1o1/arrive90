"""Monotonic simultaneous output-support eligibility discovery."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@dataclass(frozen=True)
class DiscoveryEvaluation:
    decisions_hash: str
    population_hash: str
    metrics_by_cell: Mapping[str, Mapping[str, float | int | bool]]
    failing_cells: frozenset[str]


@dataclass(frozen=True)
class DiscoveryIteration:
    iteration: int
    input_manifest_hash: str
    decisions_hash: str
    population_hash: str
    metrics_hash: str
    simultaneous_removal_set: tuple[str, ...]
    output_manifest_hash: str


@dataclass(frozen=True)
class DiscoveryArtifact:
    cell_inventory: tuple[str, ...]
    acceptance_charter_hash: str
    algorithm_hash: str
    pretest_evidence_hashes: tuple[str, ...]
    iterations: tuple[DiscoveryIteration, ...]
    final_manifest: tuple[tuple[str, bool], ...]
    final_manifest_hash: str

    @property
    def artifact_hash(self) -> str:
        return _hash(
            {
                "acceptance_charter_hash": self.acceptance_charter_hash,
                "algorithm_hash": self.algorithm_hash,
                "cell_inventory": self.cell_inventory,
                "final_manifest": self.final_manifest,
                "iterations": [iteration.__dict__ for iteration in self.iterations],
                "pretest_evidence_hashes": self.pretest_evidence_hashes,
            }
        )


DiscoveryWorker = Callable[[Mapping[str, bool]], DiscoveryEvaluation]


def discover_eligibility(
    cells: tuple[str, ...],
    worker: DiscoveryWorker,
    *,
    acceptance_charter_hash: str,
    algorithm_hash: str,
    pretest_evidence_hashes: tuple[str, ...],
) -> DiscoveryArtifact:
    """Remove all failing eligible cells simultaneously until a verified fixed point."""

    inventory = tuple(sorted(set(cells), key=str.encode))
    if len(inventory) != len(cells) or not inventory:
        raise ValueError("discovery cells must be nonempty and unique")
    manifest = dict.fromkeys(inventory, True)
    iterations: list[DiscoveryIteration] = []
    for iteration_index in range(len(inventory) + 1):
        input_hash = _hash(manifest)
        evaluation = worker(dict(manifest))
        unknown = evaluation.failing_cells - manifest.keys()
        if unknown:
            raise ValueError(f"discovery worker returned unknown cells: {sorted(unknown)}")
        removals = tuple(
            sorted(
                (cell for cell in evaluation.failing_cells if manifest[cell]),
                key=str.encode,
            )
        )
        output = dict(manifest)
        for cell in removals:
            output[cell] = False
        output_hash = _hash(output)
        iterations.append(
            DiscoveryIteration(
                iteration_index,
                input_hash,
                evaluation.decisions_hash,
                evaluation.population_hash,
                _hash(evaluation.metrics_by_cell),
                removals,
                output_hash,
            )
        )
        manifest = output
        if not removals:
            verification = worker(dict(manifest))
            if verification != evaluation:
                raise ValueError("fresh fixed-point verification did not reproduce evaluation")
            break
    else:
        raise RuntimeError("eligibility discovery exceeded the N + 1 termination guard")
    final = tuple((cell, manifest[cell]) for cell in inventory)
    return DiscoveryArtifact(
        inventory,
        acceptance_charter_hash,
        algorithm_hash,
        tuple(sorted(pretest_evidence_hashes)),
        tuple(iterations),
        final,
        _hash(dict(final)),
    )
