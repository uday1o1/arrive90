from __future__ import annotations

import sqlite3
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from arrive90_data_contracts.candidates import CandidateItinerary, TransitLeg
from arrive90_decision.contracts import (
    CandidateScore,
    DecisionContext,
    EligibilityManifest,
    HorizonSupportManifest,
    QuantileEstimate,
    RecoveryTriggerInput,
    ScoringState,
    TripState,
)
from arrive90_service.app import create_app
from arrive90_service.contracts import (
    FeedStatus,
    NormalizedJourneyRequest,
    RecoveryMaterials,
    RecoveryRequest,
    SearchMaterials,
    ServiceConfig,
    Station,
)
from arrive90_service.store import CapabilityTripStore
from fastapi.testclient import TestClient
from httpx2 import Response

NOW = datetime(2025, 1, 1, 12, tzinfo=UTC)
ORIGIN = "http://testserver"


def _config(**changes: object) -> ServiceConfig:
    values: dict[str, object] = {
        "allowed_hosts": frozenset({"testserver"}),
        "allowed_origins": frozenset({ORIGIN}),
        "decision_keys": (("d1", b"d" * 32),),
        "active_decision_key_version": "d1",
        "trip_keys": (("t1", b"t" * 32),),
        "active_trip_key_version": "t1",
    }
    values.update(changes)
    return ServiceConfig(**values)  # type: ignore[arg-type]


def _direct(request: NormalizedJourneyRequest) -> CandidateItinerary:
    leg = TransitLeg(
        "direct-pattern",
        "Red",
        0,
        "direct-trip",
        "a-platform",
        "a",
        "b-platform",
        "b",
        request.effective_ready_at_utc + timedelta(minutes=1),
        request.effective_ready_at_utc + timedelta(minutes=12),
        ("a-platform", "b-platform"),
    )
    return CandidateItinerary((leg,), ())


def _transfer(request: NormalizedJourneyRequest) -> CandidateItinerary:
    first = TransitLeg(
        "first-pattern",
        "Red",
        0,
        "first-trip",
        "a-platform",
        "a",
        "x-platform-1",
        "x",
        request.effective_ready_at_utc + timedelta(minutes=1),
        request.effective_ready_at_utc + timedelta(minutes=8),
        ("a-platform", "x-platform-1"),
    )
    second = TransitLeg(
        "second-pattern",
        "Orange",
        1,
        "second-trip",
        "x-platform-2",
        "x",
        "b-platform",
        "b",
        request.effective_ready_at_utc + timedelta(minutes=10),
        request.effective_ready_at_utc + timedelta(minutes=16),
        ("x-platform-2", "b-platform"),
    )
    return CandidateItinerary((first, second), (60,))


class Backend:
    def __init__(self) -> None:
        self.calls: list[NormalizedJourneyRequest] = []
        self.cutoff_offset = timedelta(0)
        self.scoring_state = ScoringState.READY
        self.recovery_calls: list[RecoveryRequest] = []

    def stations(self) -> tuple[Station, ...]:
        return (Station("a", "Alpha"), Station("b", "Bravo"))

    def search(self, request: NormalizedJourneyRequest) -> SearchMaterials:
        self.calls.append(request)
        direct = _direct(request)
        transfer = _transfer(request)
        scores = (
            CandidateScore(
                direct,
                0.75,
                "band-low",
                ("line-red", "station-a", "station-b"),
            ),
            CandidateScore(
                transfer,
                0.95,
                "band-high",
                ("line-red", "line-orange", "station-a", "station-b", "station-x"),
                (
                    QuantileEstimate(
                        "p90",
                        request.effective_ready_at_utc + timedelta(minutes=18),
                        "quantile-p90",
                    ),
                ),
            ),
        )
        cells = frozenset(
            cell
            for score in scores
            for cell in (score.prediction_band_cell_id, *score.applicable_slice_cell_ids)
        ) | {"quantile-p90"}
        slack = int(
            (request.effective_deadline_at_utc - request.effective_ready_at_utc).total_seconds()
            // 60
        )
        return SearchMaterials(
            scores,
            DecisionContext(
                request.initial_query_cutoff_utc + self.cutoff_offset,
                "context-v1",
                "ALERT_MASK_V1",
                "candidate-manifest",
                tuple((score.itinerary.policy_key, True) for score in scores),
            ),
            EligibilityManifest(cells, cells),
            HorizonSupportManifest(frozenset({f"slack-{slack}"})),
            self.scoring_state,
            FeedStatus.FRESH,
            "synthetic-qualified-model-v1",
            "historical_v1",
            source_attempt_lineage=("attempt-vp", "attempt-alert"),
        )

    def recovery(self, request: RecoveryRequest) -> RecoveryMaterials:
        self.recovery_calls.append(request)
        continuation = _recovery_candidate(request.recovery_cutoff_utc, 0, 20)
        alternative = _recovery_candidate(request.recovery_cutoff_utc, 1, 15)
        candidates = (continuation, alternative)
        return RecoveryMaterials(
            candidates,
            continuation.policy_key,
            DecisionContext(
                request.recovery_cutoff_utc,
                "recovery-context-v1",
                "ALERT_MASK_V1",
                "recovery-manifest",
                tuple((candidate.policy_key, True) for candidate in candidates),
            ),
            RecoveryTriggerInput(
                TripState.AT_TRANSFER,
                True,
                None,
                False,
                False,
                False,
                True,
                False,
            ),
        )


def _recovery_candidate(
    cutoff: datetime,
    index: int,
    arrival_minutes: int,
) -> CandidateItinerary:
    leg = TransitLeg(
        f"recovery-pattern-{index}",
        "Orange",
        1,
        f"recovery-trip-{index}",
        "x-platform-2",
        "x",
        "b-platform",
        "b",
        cutoff + timedelta(minutes=index + 1),
        cutoff + timedelta(minutes=arrival_minutes),
        ("x-platform-2", "b-platform"),
    )
    return CandidateItinerary((leg,), ())


def _client(
    tmp_path: Path,
    *,
    config: ServiceConfig | None = None,
    backend: Backend | None = None,
) -> tuple[TestClient, Backend, CapabilityTripStore]:
    used_backend = backend or Backend()
    used_config = config or _config()
    store = CapabilityTripStore(tmp_path / f"state-{uuid.uuid4()}.sqlite3", used_config)
    app = create_app(
        backend=used_backend,
        store=store,
        config=used_config,
        clock=lambda: NOW,
        epoch_clock=lambda: 100.0,
    )
    return TestClient(app), used_backend, store


def _search_payload(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "origin_station_id": "a",
        "destination_station_id": "b",
        "ready_at": NOW.isoformat(),
        "deadline": (NOW + timedelta(minutes=30)).isoformat(),
        "reliability_target": "0.90",
        "maximum_extra_minutes": 20,
    }
    payload.update(changes)
    return payload


def _search(client: TestClient, **changes: object) -> Response:
    return client.post(
        "/v1/journeys/search",
        json=_search_payload(**changes),
        headers={"Origin": ORIGIN},
    )


def test_public_search_uses_one_cutoff_and_suppresses_nonselected_outputs(tmp_path: Path) -> None:
    client, backend, store = _client(tmp_path)
    response = _search(client)
    assert response.status_code == 200
    body = response.json()
    assert len(backend.calls) == 1
    assert backend.calls[0].initial_query_cutoff_utc == NOW
    assert body["data_cutoff"] == "2025-01-01T12:00:00Z"
    assert body["target_status"] == "TARGET_MET"
    assert body["recommended_itinerary"]["transfer_count"] == 1
    assert body["recommended_itinerary"]["deadline_probability"] == "0.950000"
    assert body["recommended_itinerary"]["arrival_quantiles"]["p90"].endswith("Z")
    assert body["fastest_itinerary"]["deadline_probability"] is None
    assert body["fastest_itinerary"]["model_output_status"] == ("NOT_SELECTED_OUTPUT_UNVALIDATED")
    assert all(item["deadline_probability"] is None for item in body["alternatives"])
    assert body["decision_id"] is not None
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-security-policy"].startswith("default-src 'self'")
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert "*" not in response.headers["access-control-allow-origin"]
    store.close()


def test_past_ready_deadline_grid_and_future_schedule_only_are_visible(tmp_path: Path) -> None:
    client, _backend, store = _client(tmp_path)
    normalized = _search(
        client,
        ready_at=(NOW - timedelta(minutes=1)).isoformat(),
        deadline=(NOW + timedelta(minutes=31, seconds=30)).isoformat(),
    ).json()
    assert normalized["ready_time_status"] == "NORMALIZED_TO_CUTOFF"
    assert normalized["deadline_time_status"] == "NORMALIZED_DOWN_TO_SUPPORTED_GRID"
    assert normalized["effective_ready_at"] == "2025-01-01T12:00:00Z"
    assert normalized["effective_deadline_at"] == "2025-01-01T12:30:00Z"
    future = _search(
        client,
        ready_at=(NOW + timedelta(minutes=16)).isoformat(),
        deadline=(NOW + timedelta(minutes=46)).isoformat(),
    ).json()
    assert future["target_status"] == "DEGRADED_SCHEDULE_ONLY"
    assert future["model_version"] == "STATIC_SCHEDULE_BASELINE_V1"
    assert future["support_status"] == "UNSUPPORTED_READY_HORIZON"
    assert future["decision_id"] is None
    assert future["trip_start_supported"] is False
    assert future["recommended_itinerary"]["deadline_probability"] is None
    store.close()


def test_search_trip_state_sse_and_stop_follow_real_public_path(tmp_path: Path) -> None:
    client, _backend, store = _client(tmp_path)
    search = _search(client).json()
    wrong = client.post(
        "/v1/trips",
        json={
            "decision_id": search["decision_id"],
            "selected_itinerary_id": search["fastest_itinerary"]["itinerary_id"],
        },
        headers={"Origin": ORIGIN},
    )
    assert wrong.status_code == 401
    created_response = client.post(
        "/v1/trips",
        json={
            "decision_id": search["decision_id"],
            "selected_itinerary_id": search["recommended_itinerary"]["itinerary_id"],
        },
        headers={"Origin": ORIGIN},
    )
    assert created_response.status_code == 200
    created = created_response.json()
    auth = {"Authorization": f"Bearer {created['trip_bearer']}"}
    read = client.get(f"/v1/trips/{created['trip_id']}", headers=auth)
    assert read.status_code == 200
    assert read.json()["state"] == "NOT_STARTED"
    mutation = {
        "idempotency_key": str(uuid.uuid4()),
        "expected_state_version": 0,
        "next_state": "ON_FIRST_LEG",
        "boarded_itinerary_or_route_pattern_id": "first-pattern",
        "recovery_decision_id": None,
    }
    first = client.post(
        f"/v1/trips/{created['trip_id']}/state",
        json=mutation,
        headers={**auth, "Origin": ORIGIN},
    )
    replay = client.post(
        f"/v1/trips/{created['trip_id']}/state",
        json=mutation,
        headers={**auth, "Origin": ORIGIN},
    )
    assert first.status_code == 200
    assert first.json()["state"] == "ON_FIRST_LEG"
    assert replay.json()["idempotent_replay"] is True
    events = client.get(f"/v1/trips/{created['trip_id']}/events", headers=auth)
    assert events.status_code == 200
    assert "event: TRIP_CREATED" in events.text
    assert "event: STATE_TRANSITION_ACKNOWLEDGED" in events.text
    assert '"value_provenance":"DETERMINISTIC_STATE"' in events.text
    stop = client.post(
        f"/v1/trips/{created['trip_id']}/stop",
        json={
            "idempotency_key": str(uuid.uuid4()),
            "expected_state_version": 1,
        },
        headers={**auth, "Origin": ORIGIN},
    )
    assert stop.status_code == 200
    assert stop.json()["state"] == "ENDED"
    assert client.get(f"/v1/trips/{created['trip_id']}", headers=auth).status_code == 401
    replay_capability = client.post(
        "/v1/trips",
        json={
            "decision_id": search["decision_id"],
            "selected_itinerary_id": search["recommended_itinerary"]["itinerary_id"],
        },
        headers={"Origin": ORIGIN},
    )
    assert replay_capability.status_code == 401
    store.close()


def test_recovery_is_schedule_only_bound_and_activates_through_state_graph(
    tmp_path: Path,
) -> None:
    backend = Backend()
    config = _config()
    store = CapabilityTripStore(tmp_path / "recovery.sqlite3", config)
    app = create_app(
        backend=backend,
        recovery_backend=backend,
        store=store,
        config=config,
        clock=lambda: NOW,
        epoch_clock=lambda: 100.0,
    )
    client = TestClient(app)
    search = _search(client).json()
    created = client.post(
        "/v1/trips",
        json={
            "decision_id": search["decision_id"],
            "selected_itinerary_id": search["recommended_itinerary"]["itinerary_id"],
        },
        headers={"Origin": ORIGIN},
    ).json()
    headers = {
        "Authorization": f"Bearer {created['trip_bearer']}",
        "Origin": ORIGIN,
    }
    first = client.post(
        f"/v1/trips/{created['trip_id']}/state",
        json={
            "idempotency_key": str(uuid.uuid4()),
            "expected_state_version": 0,
            "next_state": "ON_FIRST_LEG",
            "boarded_itinerary_or_route_pattern_id": "first-pattern",
        },
        headers=headers,
    )
    assert first.status_code == 200
    at_transfer = client.post(
        f"/v1/trips/{created['trip_id']}/state",
        json={
            "idempotency_key": str(uuid.uuid4()),
            "expected_state_version": 1,
            "next_state": "AT_TRANSFER",
            "boarded_itinerary_or_route_pattern_id": None,
        },
        headers=headers,
    )
    assert at_transfer.status_code == 200
    recovery = at_transfer.json()["recovery_decision"]
    assert len(backend.recovery_calls) == 1
    assert backend.recovery_calls[0].current_station_id == "x"
    assert recovery["reason"] == "CAUSAL_CLOSURE"
    assert recovery["deadline_probability"] is None
    assert recovery["new_arrival_quantiles"] is None
    assert "target_status" not in recovery
    assert recovery["recommendation"]["deadline_probability"] is None
    activation = client.post(
        f"/v1/trips/{created['trip_id']}/state",
        json={
            "idempotency_key": str(uuid.uuid4()),
            "expected_state_version": 2,
            "next_state": "ON_FINAL_LEG",
            "boarded_itinerary_or_route_pattern_id": recovery["recommendation"]["itinerary_id"],
            "recovery_decision_id": recovery["recovery_decision_id"],
        },
        headers=headers,
    )
    assert activation.status_code == 200
    assert activation.json()["state"] == "ON_FINAL_LEG"
    replay_recovery = client.post(
        f"/v1/trips/{created['trip_id']}/state",
        json={
            "idempotency_key": str(uuid.uuid4()),
            "expected_state_version": 3,
            "next_state": "ON_FINAL_LEG",
            "boarded_itinerary_or_route_pattern_id": recovery["recommendation"]["itinerary_id"],
            "recovery_decision_id": recovery["recovery_decision_id"],
        },
        headers=headers,
    )
    assert replay_recovery.status_code == 409
    events = client.get(f"/v1/trips/{created['trip_id']}/events", headers=headers)
    assert "event: RECOVERY_DECISION" in events.text
    assert '"value_provenance":"RECOVERY_SCHEDULE_ONLY"' in events.text
    store.close()


def test_boundary_authorization_validation_and_rate_limits_fail_before_backend(
    tmp_path: Path,
) -> None:
    config = _config(search_limit_per_minute=1)
    client, backend, store = _client(tmp_path, config=config)
    hostile_origin = client.post(
        "/v1/journeys/search",
        json=_search_payload(),
        headers={"Origin": "https://hostile.example"},
    )
    assert hostile_origin.status_code == 403
    invalid = _search(client, origin_station_id="<script>")
    assert invalid.status_code == 422
    assert len(backend.calls) == 0
    assert _search(client).status_code == 200
    assert _search(client).status_code == 429
    missing_bearer = client.get(f"/v1/trips/{uuid.uuid4()}")
    forged_bearer = client.get(
        f"/v1/trips/{uuid.uuid4()}",
        headers={"Authorization": f"Bearer {'x' * 43}"},
    )
    assert missing_bearer.status_code == forged_bearer.status_code == 401
    assert missing_bearer.json() == forged_bearer.json()
    store.close()


def test_host_forwarded_transport_and_body_controls(tmp_path: Path) -> None:
    client, _backend, store = _client(tmp_path)
    assert client.get("/v1/system/status", headers={"Host": "hostile"}).status_code == 400
    assert (
        client.get(
            "/v1/system/status",
            headers={"X-Forwarded-Proto": "https"},
        ).status_code
        == 400
    )
    oversized = client.post(
        "/v1/journeys/search",
        content=b"x" * (32 * 1024 + 1),
        headers={"Origin": ORIGIN, "Content-Type": "application/json"},
    )
    assert oversized.status_code == 413
    nonloopback = replace(
        _config(),
        loopback_only=False,
        allowed_origins=frozenset({"https://testserver"}),
    )
    remote_client, _remote_backend, remote_store = _client(tmp_path, config=nonloopback)
    assert remote_client.get("/v1/system/status").status_code == 400
    store.close()
    remote_store.close()


def test_backend_cannot_change_server_cutoff(tmp_path: Path) -> None:
    backend = Backend()
    backend.cutoff_offset = timedelta(seconds=1)
    client, _backend, store = _client(tmp_path, backend=backend)
    try:
        client.post(
            "/v1/journeys/search",
            json=_search_payload(),
            headers={"Origin": ORIGIN},
        )
    except RuntimeError as error:
        assert "server-owned" in str(error)
    else:
        raise AssertionError("backend cutoff mutation must fail")
    store.close()


def test_sse_database_fault_releases_stream_slot_and_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _backend, store = _client(tmp_path)
    search = _search(client).json()
    created = client.post(
        "/v1/trips",
        json={
            "decision_id": search["decision_id"],
            "selected_itinerary_id": search["recommended_itinerary"]["itinerary_id"],
        },
        headers={"Origin": ORIGIN},
    ).json()
    headers = {"Authorization": f"Bearer {created['trip_bearer']}"}
    events_after = store.events_after

    def fail(*_args: object, **_kwargs: object) -> tuple[dict[str, object], ...]:
        raise sqlite3.OperationalError("seeded SSE database failure")

    monkeypatch.setattr(store, "events_after", fail)
    failed = client.get(f"/v1/trips/{created['trip_id']}/events", headers=headers)
    assert failed.status_code == 503
    assert failed.json()["reason"] == "STATE_STORE_UNAVAILABLE"
    monkeypatch.setattr(store, "events_after", events_after)
    recovered = client.get(f"/v1/trips/{created['trip_id']}/events", headers=headers)
    assert recovered.status_code == 200
    assert "event: TRIP_CREATED" in recovered.text
    store.close()


def test_unauthorized_state_flood_cannot_consume_authorized_trip_budget(tmp_path: Path) -> None:
    config = _config(state_limit_per_minute=1)
    client, _backend, store = _client(tmp_path, config=config)
    search = _search(client).json()
    created = client.post(
        "/v1/trips",
        json={
            "decision_id": search["decision_id"],
            "selected_itinerary_id": search["recommended_itinerary"]["itinerary_id"],
        },
        headers={"Origin": ORIGIN},
    ).json()
    mutation = {
        "idempotency_key": str(uuid.uuid4()),
        "expected_state_version": 0,
        "next_state": "ON_FIRST_LEG",
        "boarded_itinerary_or_route_pattern_id": "first-pattern",
    }
    for _index in range(3):
        rejected = client.post(
            f"/v1/trips/{created['trip_id']}/state",
            json=mutation,
            headers={"Authorization": f"Bearer {'x' * 43}", "Origin": ORIGIN},
        )
        assert rejected.status_code == 401
    authorized = client.post(
        f"/v1/trips/{created['trip_id']}/state",
        json=mutation,
        headers={
            "Authorization": f"Bearer {created['trip_bearer']}",
            "Origin": ORIGIN,
        },
    )
    assert authorized.status_code == 200
    store.close()
