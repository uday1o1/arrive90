from __future__ import annotations

from pathlib import Path

from arrive90_service.app import create_app
from arrive90_service.explorer import ExplorerRepository
from fastapi.testclient import TestClient


def _client() -> tuple[ExplorerRepository, TestClient]:
    repository = ExplorerRepository.load()
    return repository, TestClient(create_app(repository=repository))


def test_read_only_explorer_api_exercises_prediction_and_reveal() -> None:
    repository, client = _client()
    replay_id = next(iter(repository.records))

    status = client.get("/v1/system/status")
    metadata = client.get("/v1/explorer/metadata")
    lines = client.get("/v1/explorer/lines")
    stations = client.get("/v1/explorer/stations")
    inventory = client.get("/v1/explorer/inventory", params={"direction_id": "1"})
    prediction = client.get(
        f"/v1/explorer/replays/{replay_id}/prediction", params={"horizon_seconds": 900}
    )
    reveal = client.get(f"/v1/explorer/replays/{replay_id}/outcome")
    reliability = client.get("/v1/explorer/reliability", params={"horizon_seconds": 900})
    evidence = client.get("/v1/explorer/evidence")

    assert status.json() == {
        "artifact_status": "READY",
        "release_mode": "LOOPBACK_LOCAL_REPLAY",
        "status": "READY",
    }
    assert metadata.status_code == 200
    assert metadata.json()["replay_count"] == 200
    assert lines.json()["lines"] == [{"line_id": "Blue", "name": "Blue Line"}]
    assert stations.json()["line_id"] == "Blue"
    assert stations.json()["stations"]
    assert inventory.status_code == 200
    assert all(row["direction_id"] == "1" for row in inventory.json()["replays"])
    assert prediction.status_code == 200
    assert prediction.json()["outcome_data_available_to_scorer"] is False
    assert reveal.status_code == 200
    assert reveal.json()["observed_after_cutoff"] is True
    assert reliability.status_code == 200
    assert reliability.json()["horizon_seconds"] == 900
    assert evidence.status_code == 200
    assert status.headers["cache-control"] == "no-store"


def test_api_errors_are_specific_and_nonrevealing(tmp_path: Path) -> None:
    repository, client = _client()
    replay_id = next(iter(repository.records))

    unsupported_line = client.get("/v1/explorer/inventory", params={"line_id": "Red"})
    unsupported_horizon = client.get(
        f"/v1/explorer/replays/{replay_id}/prediction", params={"horizon_seconds": 42}
    )
    unknown = client.get("/v1/explorer/replays/not-a-replay/prediction")
    unknown_outcome = client.get("/v1/explorer/replays/not-a-replay/outcome")
    unsupported_reliability = client.get("/v1/explorer/reliability", params={"horizon_seconds": 42})
    missing = TestClient(create_app(demo_root=tmp_path / "missing"))
    unavailable = missing.get("/v1/explorer/metadata")

    assert unsupported_line.status_code == 422
    assert "only retained line" in unsupported_line.text
    assert unsupported_horizon.status_code == 422
    assert "horizon must be one of" in unsupported_horizon.text
    assert unknown.status_code == 404
    assert "unknown held-out replay" in unknown.text
    assert unknown_outcome.status_code == 404
    assert unsupported_reliability.status_code == 422
    assert missing.get("/v1/system/status").json()["status"] == "DEGRADED"
    assert unavailable.status_code == 503
    assert unavailable.json()["detail"]["reason"] == "EXPLORER_ARTIFACT_UNAVAILABLE"


def test_frontend_routes_follow_api_routes() -> None:
    _repository, client = _client()
    page = client.get("/")
    script = client.get("/app.js")

    assert page.status_code == 200
    assert '<main id="explorer">' in page.text
    assert 'href="/app.css"' in page.text
    assert '<script type="module" src="/app.js"></script>' in page.text
    assert script.status_code == 200
    assert "/v1/explorer/replays/" in script.text
    assert client.get("/missing.js", headers={"Accept": "text/javascript"}).status_code == 404
    assert client.get("/v1/system/status").status_code == 200
