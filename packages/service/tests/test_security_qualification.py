from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
QUALIFIER = ROOT / "scripts" / "qualify_milestone_9_security.py"


def _write(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _version() -> dict[str, object]:
    return {
        "Version": "0.73.0",
        "VulnerabilityDB": {
            "DownloadedAt": "2026-08-14T00:00:00Z",
            "UpdatedAt": "2026-08-14T00:00:00Z",
        },
    }


def _image() -> dict[str, object]:
    return {
        "ArtifactID": "sha256:clean",
        "ArtifactName": "release-candidate.tar",
        "Metadata": {"ImageConfig": {"config": {"User": "65532:65532"}}},
        "Results": [],
    }


def _qualify(tmp_path: Path, repository: object, *, expected_returncode: int = 0) -> dict[str, Any]:
    output = tmp_path / "qualification.json"
    completed = subprocess.run(  # noqa: S603 - exact active Python and repository script
        [
            sys.executable,
            str(QUALIFIER),
            "--repository-report",
            str(_write(tmp_path / "repository.json", repository)),
            "--image-report",
            str(_write(tmp_path / "image.json", _image())),
            "--version-report",
            str(_write(tmp_path / "version.json", _version())),
            "--output",
            str(output),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == expected_returncode, completed.stderr
    value = json.loads(output.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_clean_security_evidence_passes(tmp_path: Path) -> None:
    report = _qualify(tmp_path, {"Results": []})

    assert report["status"] == "PASSED"
    assert report["finding_counts"] == {
        "misconfigurations": 0,
        "secrets": 0,
        "vulnerabilities": 0,
    }


def test_seeded_high_vulnerability_fails_for_intended_reason(tmp_path: Path) -> None:
    repository = {
        "Results": [
            {
                "Target": "uv.lock",
                "Vulnerabilities": [
                    {
                        "PkgName": "seeded-package",
                        "Severity": "HIGH",
                        "VulnerabilityID": "CVE-SEEDED-0001",
                    }
                ],
            }
        ]
    }
    report = _qualify(tmp_path, repository, expected_returncode=1)

    assert report["status"] == "FAILED"
    assert report["failing_checks"] == ["repository_has_no_critical_or_high_finding_or_secret"]
    assert report["repository"]["findings"]["vulnerabilities"] == [
        {
            "id": "CVE-SEEDED-0001",
            "package": "seeded-package",
            "severity": "HIGH",
            "target": "uv.lock",
        }
    ]
