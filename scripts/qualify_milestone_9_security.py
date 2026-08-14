"""Summarize pinned Trivy and Ruff evidence without retaining generated raw scans."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TRIVY_VERSION = "0.73.0"
TRIVY_IMAGE = (
    "aquasec/trivy@sha256:7cced7cae583819fc7806d4cbc0dbbc7cad18b99f7d3e235192e6da8c091045c"
)


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _findings(report: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    findings: dict[str, list[dict[str, str]]] = {
        "misconfigurations": [],
        "secrets": [],
        "vulnerabilities": [],
    }
    for result in report.get("Results") or ():
        target = str(result.get("Target", "UNKNOWN"))
        for vulnerability in result.get("Vulnerabilities") or ():
            severity = str(vulnerability.get("Severity", "UNKNOWN")).upper()
            if severity in {"CRITICAL", "HIGH"}:
                findings["vulnerabilities"].append(
                    {
                        "id": str(vulnerability.get("VulnerabilityID", "UNKNOWN")),
                        "package": str(vulnerability.get("PkgName", "UNKNOWN")),
                        "severity": severity,
                        "target": target,
                    }
                )
        for misconfiguration in result.get("Misconfigurations") or ():
            severity = str(misconfiguration.get("Severity", "UNKNOWN")).upper()
            if severity in {"CRITICAL", "HIGH"}:
                findings["misconfigurations"].append(
                    {
                        "id": str(misconfiguration.get("ID", "UNKNOWN")),
                        "severity": severity,
                        "target": target,
                    }
                )
        for secret in result.get("Secrets") or ():
            findings["secrets"].append(
                {
                    "id": str(secret.get("RuleID", "UNKNOWN")),
                    "severity": str(secret.get("Severity", "UNKNOWN")).upper(),
                    "target": target,
                }
            )
    for members in findings.values():
        members.sort(key=lambda item: json.dumps(item, sort_keys=True).encode())
    return findings


def _licenses(report: dict[str, Any]) -> tuple[str, ...]:
    names = {
        str(license_item.get("Name"))
        for result in report.get("Results") or ()
        for license_item in result.get("Licenses") or ()
        if license_item.get("Name")
    }
    return tuple(sorted(names, key=str.encode))


def _image_user(report: dict[str, Any]) -> str | None:
    metadata = report.get("Metadata") or {}
    image_config = metadata.get("ImageConfig") or {}
    config = image_config.get("config") or image_config.get("Config") or {}
    user = config.get("User")
    return str(user) if user else None


def build_report(
    *, repository_report_path: Path, image_report_path: Path, version_report_path: Path
) -> dict[str, Any]:
    repository = _load(repository_report_path)
    image = _load(image_report_path)
    version = _load(version_report_path)
    repository_findings = _findings(repository)
    image_findings = _findings(image)
    ruff = str(Path(sys.executable).with_name("ruff"))
    static = subprocess.run(  # noqa: S603 - exact locked-environment Ruff executable
        [ruff, "check", "--select", "S", "packages", "scripts", "tools", "benchmarks"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    all_findings = {
        key: len(repository_findings[key]) + len(image_findings[key]) for key in repository_findings
    }
    database = version.get("VulnerabilityDB") or {}
    checks = {
        "container_has_no_critical_or_high_finding": not any(
            image_findings[key] for key in image_findings
        ),
        "container_runs_as_non_root_uid": _image_user(image) == "65532:65532",
        "repository_has_no_critical_or_high_finding_or_secret": not any(
            repository_findings[key] for key in repository_findings
        ),
        "ruff_security_static_analysis_passed": static.returncode == 0,
        "scanner_database_metadata_present": bool(
            database.get("UpdatedAt") and database.get("DownloadedAt")
        ),
        "trivy_version_is_exact": version.get("Version") == TRIVY_VERSION,
    }
    return {
        "checks": checks,
        "evidence_kind": "LOCAL_SECURITY_QUALIFICATION",
        "failing_checks": sorted(key for key, value in checks.items() if not value),
        "finding_counts": all_findings,
        "image": {
            "artifact_id": image.get("ArtifactID"),
            "artifact_name": image.get("ArtifactName"),
            "findings": image_findings,
            "report_sha256": _digest(image_report_path),
            "runtime_user": _image_user(image),
        },
        "trivy_license_findings": sorted(
            set(_licenses(repository)) | set(_licenses(image)), key=str.encode
        ),
        "repository": {
            "findings": repository_findings,
            "report_sha256": _digest(repository_report_path),
        },
        "scanner": {
            "database": database,
            "image": TRIVY_IMAGE,
            "version": version.get("Version"),
            "version_report_sha256": _digest(version_report_path),
        },
        "static_analysis": {
            "command": "ruff check --select S packages scripts tools benchmarks",
            "stderr": static.stderr.strip(),
            "stdout": static.stdout.strip(),
        },
        "status": "PASSED" if all(checks.values()) else "FAILED",
    }


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument("--repository-report", type=Path, required=True)
    command.add_argument("--image-report", type=Path, required=True)
    command.add_argument("--version-report", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    return command


def main() -> int:
    args = parser().parse_args()
    report = build_report(
        repository_report_path=args.repository_report,
        image_report_path=args.image_report,
        version_report_path=args.version_report,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(args.output)
    return 0 if report["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
