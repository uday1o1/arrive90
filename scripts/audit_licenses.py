"""Create a deterministic license and attribution inventory from locked inputs."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import re
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LOCK_METADATA_OVERRIDES = {
    ("colorama", "0.4.6"): (
        "BSD-3-Clause",
        "https://pypi.org/pypi/colorama/0.4.6/json",
    ),
    ("httpx2-jsfetch", "1.0"): (
        "BSD-3-Clause",
        "https://pypi.org/pypi/httpx2-jsfetch/1.0/json",
    ),
    ("nvidia-nccl-cu12", "2.31.2"): (
        "BSD-3-Clause",
        "https://pypi.org/pypi/nvidia-nccl-cu12/2.31.2/json",
    ),
}


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalized_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _metadata_license(metadata: importlib.metadata.PackageMetadata) -> str | None:
    expression = metadata.get("License-Expression")
    if expression:
        return expression.strip()
    classifiers = [
        classifier.removeprefix("License :: OSI Approved :: ").strip()
        for classifier in metadata.get_all("Classifier", [])
        if classifier.startswith("License :: OSI Approved :: ")
    ]
    if classifiers:
        return " OR ".join(sorted(set(classifiers), key=str.encode))
    license_text = (metadata.get("License") or "").strip()
    if license_text and len(license_text) <= 160:
        return license_text
    return None


def _python_packages(lock_path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    packages: list[dict[str, Any]] = []
    unresolved: list[str] = []
    for locked in lock["package"]:
        name = _normalized_name(str(locked["name"]))
        version = str(locked["version"])
        installed = True
        source = "installed-core-metadata"
        try:
            metadata = importlib.metadata.metadata(name)
            observed_version = importlib.metadata.version(name)
            license_name = "MIT" if name == "arrive90" else _metadata_license(metadata)
        except importlib.metadata.PackageNotFoundError:
            installed = False
            observed_version = None
            override = LOCK_METADATA_OVERRIDES.get((name, version))
            license_name = override[0] if override else None
            source = override[1] if override else "unresolved"
        if observed_version not in {None, version} or license_name is None:
            unresolved.append(f"{name}=={version}")
        packages.append(
            {
                "installed_in_audit_environment": installed,
                "license": license_name,
                "license_metadata_source": source,
                "name": name,
                "version": version,
            }
        )
    return packages, sorted(unresolved, key=str.encode)


def _node_packages(lock_path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    packages: list[dict[str, Any]] = []
    unresolved: list[str] = []
    for package_path, package in sorted(
        lock["packages"].items(), key=lambda item: item[0].encode()
    ):
        if not package_path:
            continue
        name = package_path.removeprefix("node_modules/")
        license_name = package.get("license")
        integrity = package.get("integrity")
        version = package.get("version")
        if not all((license_name, integrity, version)):
            unresolved.append(name)
        packages.append(
            {
                "integrity": integrity,
                "license": license_name,
                "name": name,
                "version": version,
            }
        )
    return packages, sorted(unresolved, key=str.encode)


def build_report() -> dict[str, Any]:
    python_lock = ROOT / "uv.lock"
    node_lock = ROOT / "package-lock.json"
    project_license = ROOT / "LICENSE"
    data_license = ROOT / "DATA_LICENSE.md"
    python_packages, unresolved_python = _python_packages(python_lock)
    node_packages, unresolved_node = _node_packages(node_lock)
    attribution = data_license.read_text(encoding="utf-8")
    checks = {
        "data_provider_attribution_present": all(
            marker in attribution for marker in ("MassDOT", "MBTA", "not affiliated")
        ),
        "node_lock_packages_have_license_and_integrity": not unresolved_node,
        "project_license_present": project_license.is_file(),
        "python_lock_packages_have_license_metadata": not unresolved_python,
        "transit_data_license_documented": data_license.is_file(),
    }
    return {
        "checks": checks,
        "failing_checks": sorted(key for key, value in checks.items() if not value),
        "inputs": {
            "data_license_sha256": _digest(data_license),
            "node_lock_sha256": _digest(node_lock),
            "project_license_sha256": _digest(project_license),
            "python_lock_sha256": _digest(python_lock),
        },
        "node_packages": node_packages,
        "python_packages": python_packages,
        "status": "PASSED" if all(checks.values()) else "FAILED",
        "unresolved_node_packages": unresolved_node,
        "unresolved_python_packages": unresolved_python,
    }


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument("--output", type=Path, required=True)
    return command


def main() -> int:
    args = parser().parse_args()
    report = build_report()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(args.output)
    return 0 if report["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
