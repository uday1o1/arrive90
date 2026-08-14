from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from arrive90_service.cli import main
from arrive90_service.contracts import NormalizedJourneyRequest
from arrive90_service.demo import LocalBlockedBackend

NOW = datetime(2025, 1, 1, 12, tzinfo=UTC)


def test_default_backend_is_working_but_suppresses_unaccepted_reliability() -> None:
    backend = LocalBlockedBackend()
    materials = backend.search(
        NormalizedJourneyRequest(
            "demo-origin",
            "demo-destination",
            NOW,
            NOW,
            NOW + timedelta(minutes=30),
            NOW + timedelta(minutes=30),
            Decimal("0.90"),
            20,
            NOW,
            "AS_REQUESTED",
            "AS_REQUESTED",
            (),
        )
    )
    assert len(backend.stations()) == 2
    assert materials.model_version == "NO_ACCEPTED_MODEL"
    assert not materials.eligibility_manifest.eligible_cells


def test_cli_rejects_nonloopback_and_invalid_port(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--host", "0.0.0.0"]) == 2  # noqa: S104 - verifies rejection
    assert "non-loopback" in capsys.readouterr().err
    assert main(["--port", "70000"]) == 2
    assert "port" in capsys.readouterr().err
