from pathlib import Path

from arrive90_service.app import create_app
from arrive90_service.contracts import ServiceConfig
from arrive90_service.demo import LocalBlockedBackend
from arrive90_service.store import CapabilityTripStore
from fastapi.testclient import TestClient


def test_frontend_is_served_after_api_routes_with_external_assets(tmp_path: Path) -> None:
    config = ServiceConfig(
        allowed_hosts=frozenset({"testserver"}),
        allowed_origins=frozenset({"http://testserver"}),
        decision_keys=(("d", b"d" * 32),),
        active_decision_key_version="d",
        trip_keys=(("t", b"t" * 32),),
        active_trip_key_version="t",
    )
    store = CapabilityTripStore(tmp_path / "frontend.sqlite3", config)
    client = TestClient(create_app(backend=LocalBlockedBackend(), store=store, config=config))
    page = client.get("/")
    assert page.status_code == 200
    assert "<main>" in page.text
    assert 'href="/app.css"' in page.text
    assert '<script type="module" src="/app.js"></script>' in page.text
    assert "unsafe-inline" not in page.headers["content-security-policy"]
    script = client.get("/app.js")
    assert script.status_code == 200
    assert "localStorage" not in script.text
    assert "innerHTML" not in script.text
    assert client.get("/missing.js", headers={"Accept": "text/javascript"}).status_code == 404
    assert client.get("/v1/system/status").json()["release_mode"] == "LOOPBACK_LOCAL"
    store.close()
