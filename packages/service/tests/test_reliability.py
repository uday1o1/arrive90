from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from arrive90_service.app import create_app
from arrive90_service.contracts import (
    ModelUnavailableError,
    NormalizedJourneyRequest,
    RouterUnavailableError,
    SearchMaterials,
    ServiceConfig,
    SourceUnavailableError,
)
from arrive90_service.demo import LocalBlockedBackend
from arrive90_service.observability import AuditEvent
from arrive90_service.reliability import ResilientJourneyBackend
from arrive90_service.store import CapabilityTripStore
from fastapi.testclient import TestClient

NOW = datetime(2025, 1, 1, 12, tzinfo=UTC)


def _config(**changes: object) -> ServiceConfig:
    values: dict[str, object] = {
        "allowed_hosts": frozenset({"testserver"}),
        "allowed_origins": frozenset({"http://testserver"}),
        "decision_keys": (("d1", b"d" * 32),),
        "active_decision_key_version": "d1",
        "trip_keys": (("t1", b"t" * 32),),
        "active_trip_key_version": "t1",
    }
    values.update(changes)
    return ServiceConfig(**values)  # type: ignore[arg-type]


def _request() -> NormalizedJourneyRequest:
    return NormalizedJourneyRequest(
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


class FailingBackend(LocalBlockedBackend):
    def __init__(self, error: Exception | None) -> None:
        self.error = error

    def search(self, request: NormalizedJourneyRequest) -> SearchMaterials:
        if self.error is not None:
            raise self.error
        return super().search(request)


@pytest.mark.parametrize("failure", [SourceUnavailableError(), ModelUnavailableError()])
def test_source_and_model_failures_use_named_schedule_fallback(failure: Exception) -> None:
    fallback = LocalBlockedBackend()
    backend = ResilientJourneyBackend(FailingBackend(failure), fallback)
    materials = backend.search(_request())
    assert materials.model_version == "NO_ACCEPTED_MODEL"
    assert materials.feed_status.value == "ABSENT"


def test_router_failure_is_stable_and_does_not_fabricate_a_route(tmp_path: Path) -> None:
    config = _config()
    store = CapabilityTripStore(tmp_path / "router.sqlite3", config)
    backend = FailingBackend(RouterUnavailableError())
    app = create_app(
        backend=backend,
        store=store,
        config=config,
        clock=lambda: NOW,
        epoch_clock=lambda: 100,
    )
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/v1/journeys/search",
        json={
            "origin_station_id": "demo-origin",
            "destination_station_id": "demo-destination",
            "ready_at": NOW.isoformat(),
            "deadline": (NOW + timedelta(minutes=30)).isoformat(),
            "reliability_target": "0.90",
            "maximum_extra_minutes": 20,
        },
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 503
    assert response.json() == {
        "detail": "service temporarily unavailable",
        "reason": "ROUTER_UNAVAILABLE",
    }
    assert response.headers["cache-control"] == "no-store"
    backend.error = None
    recovered = client.post(
        "/v1/journeys/search",
        json={
            "origin_station_id": "demo-origin",
            "destination_station_id": "demo-destination",
            "ready_at": NOW.isoformat(),
            "deadline": (NOW + timedelta(minutes=30)).isoformat(),
            "reliability_target": "0.90",
            "maximum_extra_minutes": 20,
        },
        headers={"Origin": "http://testserver"},
    )
    assert recovered.status_code == 200
    store.close()


def test_database_failure_is_constant_shape_and_nonrevealing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config()
    store = CapabilityTripStore(tmp_path / "database.sqlite3", config)
    app = create_app(
        backend=LocalBlockedBackend(),
        store=store,
        config=config,
        clock=lambda: NOW,
        epoch_clock=lambda: 100,
    )

    def unavailable(*_args: object, **_kwargs: object) -> None:
        raise sqlite3.OperationalError("injected database path and secret")

    authorize_trip = store.authorize_trip
    monkeypatch.setattr(store, "authorize_trip", unavailable)
    response = TestClient(app, raise_server_exceptions=False).get(
        f"/v1/trips/{uuid.uuid4()}",
        headers={"Authorization": f"Bearer {'x' * 43}"},
    )
    assert response.status_code == 503
    assert response.json() == {
        "detail": "service temporarily unavailable",
        "reason": "STATE_STORE_UNAVAILABLE",
    }
    assert "injected" not in response.text
    monkeypatch.setattr(store, "authorize_trip", authorize_trip)
    recovered = TestClient(app).get(
        f"/v1/trips/{uuid.uuid4()}",
        headers={"Authorization": f"Bearer {'x' * 43}"},
    )
    assert recovered.status_code == 401
    store.close()


def test_clock_regression_and_malformed_trusted_forwarding_fail_closed(tmp_path: Path) -> None:
    values = iter((NOW, NOW - timedelta(seconds=10), NOW + timedelta(seconds=1)))
    config = _config()
    store = CapabilityTripStore(tmp_path / "clock.sqlite3", config)
    app = create_app(
        backend=LocalBlockedBackend(),
        store=store,
        config=config,
        clock=lambda: next(values),
    )
    client = TestClient(app)
    assert client.get("/v1/system/status").status_code == 200
    regressed = client.get("/v1/system/status")
    assert regressed.status_code == 503
    assert regressed.json() == {"detail": "request rejected"}
    assert client.get("/v1/system/status").status_code == 200
    release = replace(
        _config(),
        loopback_only=False,
        allowed_origins=frozenset({"https://testserver"}),
        trusted_proxy_addresses=frozenset({"10.0.0.2"}),
    )
    release_store = CapabilityTripStore(tmp_path / "proxy.sqlite3", release)
    release_app = create_app(
        backend=LocalBlockedBackend(),
        store=release_store,
        config=release,
        clock=lambda: NOW,
    )
    proxy_client = TestClient(release_app, client=("10.0.0.2", 50_000))
    good = proxy_client.get(
        "/v1/system/status",
        headers={"X-Forwarded-For": "203.0.113.9", "X-Forwarded-Proto": "https"},
    )
    assert good.status_code == 200
    assert good.json()["release_mode"] == "RELEASE_BOUNDARY"
    assert good.headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"
    assert good.headers["referrer-policy"] == "no-referrer"
    assert good.headers["x-content-type-options"] == "nosniff"
    assert good.headers["x-frame-options"] == "DENY"
    assert "geolocation=()" in good.headers["permissions-policy"]
    assert "unsafe-inline" not in good.headers["content-security-policy"]
    assert "unsafe-eval" not in good.headers["content-security-policy"]
    bad = proxy_client.get(
        "/v1/system/status",
        headers={
            "X-Forwarded-For": "203.0.113.9, 198.51.100.4",
            "X-Forwarded-Proto": "https",
        },
    )
    assert bad.status_code == 400
    store.close()
    release_store.close()


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"allowed_hosts": frozenset({"*"})}, "Host"),
        ({"allowed_origins": frozenset({"*"})}, "Origin"),
        ({"maximum_body_bytes": 32 * 1024 + 1}, "frozen V1"),
        ({"trip_ttl_seconds": 6 * 60 * 60 + 1}, "frozen V1"),
        ({"trusted_proxy_addresses": frozenset({"proxy.local"})}, "IP addresses"),
        (
            {
                "decision_keys": (
                    ("d1", b"d" * 32),
                    ("d2", b"e" * 32),
                    ("d3", b"f" * 32),
                )
            },
            "keyring",
        ),
    ],
)
def test_release_configuration_cannot_weaken_frozen_bounds(
    change: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        _config(**change)


def test_running_lifespan_deletes_expired_trip_state(tmp_path: Path) -> None:
    now = [100.0]
    config = _config(trip_ttl_seconds=1, cleanup_interval_seconds=1)
    store = CapabilityTripStore(tmp_path / "expiry.sqlite3", config)
    selected = "a" * 64
    issued = store.issue_decision(
        {
            "candidate_generator_version": "STATIC_ROUTE_POLICY_V1",
            "data_cutoff": "2025-01-01T12:00:00Z",
            "decision_context_id": "context",
            "feature_schema_version": "historical_v1",
            "feed_status": "FRESH",
            "model_version": "model-v1",
            "selected_itinerary": {
                "allowed_boarding_ids": [selected],
                "itinerary_id": selected,
                "transfer_count": 0,
            },
            "source_attempt_lineage": ["attempt"],
            "static_candidate_manifest_hash": "manifest",
        },
        recommended_itinerary_id=selected,
        now=now[0],
    )
    store.consume_and_create_trip(
        issued.capability,
        selected_itinerary_id=selected,
        now=now[0],
    )
    app = create_app(
        backend=LocalBlockedBackend(),
        store=store,
        config=config,
        clock=lambda: NOW,
        epoch_clock=lambda: now[0],
    )
    with TestClient(app):
        now[0] = 102
        time.sleep(1.2)
        assert store._connection.execute("SELECT count(*) FROM trips").fetchone()[0] == 0
    store.close()


def test_wired_access_audit_excludes_inputs_and_trip_identifiers(tmp_path: Path) -> None:
    config = _config()
    store = CapabilityTripStore(tmp_path / "audit.sqlite3", config)
    events: list[AuditEvent] = []
    app = create_app(
        backend=LocalBlockedBackend(),
        store=store,
        config=config,
        clock=lambda: NOW,
        audit_sink=events.append,
    )
    trip_id = str(uuid.uuid4())
    response = TestClient(app).get(
        f"/v1/trips/{trip_id}",
        headers={"Authorization": f"Bearer {'s' * 43}"},
    )
    assert response.status_code == 401
    assert events[-1]["route"] == "/v1/trips/{trip_id}"
    serialized = str(events)
    assert trip_id not in serialized
    assert "ssss" not in serialized
    store.close()


def test_observability_sink_failure_does_not_break_service(tmp_path: Path) -> None:
    config = _config()
    store = CapabilityTripStore(tmp_path / "audit-failure.sqlite3", config)

    def fail(_event: AuditEvent) -> None:
        raise RuntimeError("seeded observability outage")

    app = create_app(
        backend=LocalBlockedBackend(),
        store=store,
        config=config,
        clock=lambda: NOW,
        audit_sink=fail,
    )
    assert TestClient(app).get("/v1/system/status").status_code == 200
    store.close()
