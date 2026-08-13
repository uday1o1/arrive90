from __future__ import annotations

import json
from pathlib import Path
from subprocess import CompletedProcess

import pytest
from arrive90_routing.graph_build import OTP_IMAGE, build_graph, graph_input_manifest


def _inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    gtfs = tmp_path / "input.zip"
    build = tmp_path / "build.json"
    router = tmp_path / "router.json"
    gtfs.write_bytes(b"gtfs")
    build.write_text("{}\n", encoding="utf-8")
    router.write_text("{}\n", encoding="utf-8")
    return gtfs, build, router


def test_graph_input_manifest_pins_every_build_input(tmp_path: Path) -> None:
    gtfs, build, router = _inputs(tmp_path)
    manifest = graph_input_manifest(gtfs, build, router, None)
    assert manifest["otp_image"] == OTP_IMAGE
    assert set(manifest["inputs"]) == {
        "build_config_sha256",
        "gtfs_sha256",
        "router_config_sha256",
    }


def test_graph_builder_uses_networkless_bounded_container_and_hashes_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gtfs, build, router = _inputs(tmp_path)
    output = tmp_path / "graph"

    def fake_run(command: list[str], *, check: bool) -> CompletedProcess[str]:
        assert check
        assert "--network=none" in command
        assert "--memory" in command
        assert OTP_IMAGE in command
        (output / "graph.obj").write_bytes(b"graph")
        return CompletedProcess(command, 0)

    monkeypatch.setattr("arrive90_routing.graph_build.subprocess.run", fake_run)
    manifest = build_graph(
        gtfs_archive=gtfs,
        output=output,
        build_config=build,
        router_config=router,
    )
    assert len(manifest["graph_sha256"]) == 64
    assert json.loads((output / "graph-manifest.json").read_text())["otp_image"] == OTP_IMAGE
    with pytest.raises(ValueError, match="fresh directory"):
        build_graph(
            gtfs_archive=gtfs,
            output=output,
            build_config=build,
            router_config=router,
        )
