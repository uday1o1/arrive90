"""Build a pinned OpenTripPlanner graph and emit an immutable input manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

OTP_IMAGE = (
    "docker.io/opentripplanner/opentripplanner@"
    "sha256:a7eac7da397faa9ec9dee407d4204895d24df4981500662fa6793aae0e71fd8f"
)
OTP_VERSION = "2.9.0"
OTP_COMMIT = "9babe45ffc9327933129f705c648137ecd96cdbe"


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            value.update(block)
    return value.hexdigest()


def graph_input_manifest(
    gtfs_archive: Path,
    build_config: Path,
    router_config: Path,
    osm_pbf: Path | None,
) -> dict[str, Any]:
    inputs = {
        "build_config_sha256": digest(build_config),
        "gtfs_sha256": digest(gtfs_archive),
        "router_config_sha256": digest(router_config),
    }
    if osm_pbf is not None:
        inputs["osm_sha256"] = digest(osm_pbf)
    return {
        "inputs": inputs,
        "otp_commit": OTP_COMMIT,
        "otp_image": OTP_IMAGE,
        "otp_version": OTP_VERSION,
    }


def _copy_input(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise ValueError(f"graph input is not a file: {source}")
    shutil.copyfile(source, destination)


def build_graph(
    *,
    gtfs_archive: Path,
    output: Path,
    build_config: Path,
    router_config: Path,
    osm_pbf: Path | None = None,
    cpus: int = 4,
    memory: str = "7g",
    java_heap: str = "-Xmx6g",
) -> dict[str, Any]:
    """Build a graph in a fresh output directory with network disabled."""

    if cpus <= 0:
        raise ValueError("CPU allocation must be positive")
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("graph output must be a fresh directory")
    output.mkdir(parents=True, exist_ok=True)
    _copy_input(gtfs_archive, output / "mbta-gtfs.zip")
    _copy_input(build_config, output / "build-config.json")
    _copy_input(router_config, output / "router-config.json")
    if osm_pbf is not None:
        _copy_input(osm_pbf, output / "massachusetts.osm.pbf")
    command = [
        "docker",
        "run",
        "--rm",
        "--network=none",
        "--cpus",
        str(cpus),
        "--memory",
        memory,
        "--env",
        f"JAVA_TOOL_OPTIONS={java_heap}",
        "--volume",
        f"{output.resolve()}:/var/opentripplanner",
        OTP_IMAGE,
        "--build",
        "--save",
    ]
    started_at = datetime.now(UTC)
    subprocess.run(command, check=True)  # noqa: S603
    completed_at = datetime.now(UTC)
    graph = output / "graph.obj"
    if not graph.is_file():
        raise RuntimeError("OpenTripPlanner completed without graph.obj")
    manifest = graph_input_manifest(gtfs_archive, build_config, router_config, osm_pbf)
    manifest.update(
        {
            "build_command": command,
            "build_completed_at_utc": completed_at.isoformat(),
            "build_started_at_utc": started_at.isoformat(),
            "cpu_allocation": cpus,
            "graph_sha256": digest(graph),
            "java_heap": java_heap,
            "memory_limit": memory,
        }
    )
    (output / "graph-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> int:
    root = Path(__file__).resolve().parents[4]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gtfs", type=Path, required=True)
    parser.add_argument("--osm", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--build-config", type=Path, default=root / "configs/otp/build-config.json")
    parser.add_argument(
        "--router-config", type=Path, default=root / "configs/otp/router-config.json"
    )
    arguments = parser.parse_args()
    manifest = build_graph(
        gtfs_archive=arguments.gtfs,
        output=arguments.output,
        build_config=arguments.build_config,
        router_config=arguments.router_config,
        osm_pbf=arguments.osm,
    )
    print(json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
