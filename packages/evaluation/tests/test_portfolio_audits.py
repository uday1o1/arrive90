from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_repository import (  # noqa: E402
    _old_public_claims,
    _undefined_workflow_make_targets,
)
from scripts.build_public_claims import (  # noqa: E402
    FINAL,
    PUBLIC_CLAIMS,
    _digest,
    _expected_measured_result,
    _load,
    _readme_claim_map,
    _readme_claim_map_is_exhaustive,
    _readme_section,
    public_claim_report_matches_current_evidence,
)
from scripts.build_public_claims import (  # noqa: E402
    build_report as build_public_claim_report,
)
from scripts.report_milestone_7 import _qualification_environment  # noqa: E402


def test_repository_audit_scans_tracked_service_web_artifacts(tmp_path: Path) -> None:
    relative = "packages/service/src/arrive90_service/web/status.json"
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_text('{"message": "Prospective live calibration remains pending."}\n')

    assert _old_public_claims(tmp_path, (relative,)) == ["prospective live calibration"]


def test_repository_audit_rejects_undefined_workflow_make_targets(tmp_path: Path) -> None:
    workflow = ".github/workflows/ci.yml"
    workflow_path = tmp_path / workflow
    workflow_path.parent.mkdir(parents=True)
    workflow_path.write_text(
        "steps:\n  - run: make check\n  - run: make retired-target\n",
        encoding="utf-8",
    )
    (tmp_path / "Makefile").write_text("check:\n\ttrue\n", encoding="utf-8")

    assert _undefined_workflow_make_targets(tmp_path, (workflow,)) == [
        {"path": workflow, "target": "retired-target"}
    ]


def test_readme_measured_result_section_is_fully_derived_from_final_report() -> None:
    final = _load(FINAL)
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    expected = _expected_measured_result(final, _digest(FINAL))

    assert _readme_section(readme, "Measured result") == expected
    assert _readme_section(readme.replace("94.057", "94.058"), "Measured result") != expected


def test_milestone_7_environment_binds_the_qualified_implementation_commit() -> None:
    environment = _qualification_environment(
        {"commit": "abc123", "environment": {"python": "3.12.13", "machine": "arm64"}}
    )

    assert environment == {
        "implementation_commit": "abc123",
        "machine": "arm64",
        "python": "3.12.13",
    }


def test_current_workflows_reference_only_defined_make_targets() -> None:
    workflow = ROOT / ".github/workflows/ci.yml"
    relative = (workflow.relative_to(ROOT).as_posix(),)
    assert _undefined_workflow_make_targets(ROOT, relative) == []


def test_public_claim_report_contains_exhaustive_readme_claim_map() -> None:
    final = _load(FINAL)
    final_hash = _digest(FINAL)
    claims = _readme_claim_map(final, final_hash)
    claim_ids = {claim["id"] for claim in claims}

    assert f"]({PUBLIC_CLAIMS})" in (ROOT / "README.md").read_text(encoding="utf-8")
    assert _readme_claim_map_is_exhaustive(final, final_hash, claims) is True
    assert claim_ids == {
        "aft-interval-nlls",
        "empirical-midpoint-point-difference",
        "final-test-population",
        "identified-15-minute-brier",
        "immutable-final-report",
        "monthly-nll-drift",
        "official-schedule-point-difference",
        "p50-median-interval-distance",
        "prediction-width",
    }


def test_persisted_public_claim_report_exactly_matches_current_evidence() -> None:
    path = ROOT / PUBLIC_CLAIMS
    persisted = json.loads(path.read_text(encoding="utf-8"))

    assert persisted == build_public_claim_report()
    assert public_claim_report_matches_current_evidence(persisted) is True


def test_public_claim_report_rejects_missing_changed_or_misbinding_evidence() -> None:
    report = build_public_claim_report()
    missing_claim = copy.deepcopy(report)
    missing_claim["readme_claims"].pop()
    changed_pointer_value = copy.deepcopy(report)
    changed_pointer_value["readme_claims"][0]["evidence"][0]["value"] = "changed"
    wrong_artifact_hash = copy.deepcopy(report)
    wrong_artifact_hash["readme_claims"][0]["artifact_sha256"] = "0" * 64

    assert public_claim_report_matches_current_evidence(missing_claim) is False
    assert public_claim_report_matches_current_evidence(changed_pointer_value) is False
    assert public_claim_report_matches_current_evidence(wrong_artifact_hash) is False
