from __future__ import annotations

import pytest
import uvicorn
from arrive90_service import cli


def test_cli_rejects_nonloopback_and_invalid_port(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--host", "0.0.0.0"]) == 2  # noqa: S104 - verifies rejection
    assert "loopback-only" in capsys.readouterr().err
    assert cli.main(["--port", "70000"]) == 2
    assert "port" in capsys.readouterr().err


def test_cli_serves_verified_explorer(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, int, bool]] = []

    def run(_app: object, *, host: str, port: int, access_log: bool) -> None:
        calls.append((host, port, access_log))

    monkeypatch.setattr(uvicorn, "run", run)
    assert cli.main(["--host", "localhost", "--port", "8888"]) == 0
    assert calls == [("localhost", 8888, False)]
