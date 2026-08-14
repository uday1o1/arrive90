from __future__ import annotations

import threading
import uuid

import pytest
from arrive90_decision.contracts import TripState
from arrive90_service.contracts import LiveEventKind, ServiceConfig
from arrive90_service.store import (
    AuthorizationError,
    CapabilityTripStore,
    ConflictError,
    CreatedTrip,
)


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


def _snapshot(selected: str = "a" * 64, transfer_count: int = 1) -> dict[str, object]:
    return {
        "candidate_generator_version": "STATIC_ROUTE_POLICY_V1",
        "data_cutoff": "2025-01-01T12:00:00Z",
        "decision_context_id": "context",
        "feature_schema_version": "historical_v1",
        "feed_status": "FRESH",
        "model_version": "model-v1",
        "selected_itinerary": {
            "allowed_boarding_ids": [selected, "pattern"],
            "itinerary_id": selected,
            "transfer_count": transfer_count,
        },
        "source_attempt_lineage": ["attempt"],
        "static_candidate_manifest_hash": "manifest",
    }


def _create(store: CapabilityTripStore, *, now: float = 100.0) -> CreatedTrip:
    selected = "a" * 64
    issued = store.issue_decision(_snapshot(selected), recommended_itinerary_id=selected, now=now)
    return store.consume_and_create_trip(
        issued.capability,
        selected_itinerary_id=selected,
        now=now + 1,
    )


def test_capability_is_single_use_exact_recommendation_bound_and_digest_only() -> None:
    store = CapabilityTripStore(":memory:", _config())
    selected = "a" * 64
    issued = store.issue_decision(_snapshot(selected), recommended_itinerary_id=selected, now=100)
    decision_row = store._connection.execute("SELECT * FROM decisions").fetchone()
    assert issued.capability not in str(tuple(decision_row))
    with pytest.raises(AuthorizationError):
        store.consume_and_create_trip(
            issued.capability,
            selected_itinerary_id="b" * 64,
            now=101,
        )
    created = store.consume_and_create_trip(
        issued.capability,
        selected_itinerary_id=selected,
        now=101,
    )
    with pytest.raises(AuthorizationError):
        store.consume_and_create_trip(
            issued.capability,
            selected_itinerary_id=selected,
            now=101,
        )
    trip_row = store._connection.execute("SELECT * FROM trips").fetchone()
    assert created.bearer not in str(tuple(trip_row))
    assert store._connection.execute("SELECT count(*) FROM decisions").fetchone()[0] == 0
    store.close()


def test_concurrent_consumption_creates_at_most_one_trip() -> None:
    store = CapabilityTripStore(":memory:", _config())
    selected = "a" * 64
    issued = store.issue_decision(_snapshot(selected), recommended_itinerary_id=selected, now=100)
    outcomes: list[str] = []

    def consume() -> None:
        try:
            store.consume_and_create_trip(
                issued.capability,
                selected_itinerary_id=selected,
                now=101,
            )
            outcomes.append("created")
        except AuthorizationError:
            outcomes.append("rejected")

    threads = [threading.Thread(target=consume) for _index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == ["created", "rejected"]
    store.close()


def test_authorization_transition_idempotency_and_atomic_outbox() -> None:
    store = CapabilityTripStore(":memory:", _config())
    created = _create(store)
    trip = store.authorize_trip(created.trip_id, created.bearer, now=102)
    assert trip.state is TripState.NOT_STARTED
    with pytest.raises(AuthorizationError):
        store.authorize_trip(created.trip_id, "x" * 43, now=102)
    key = str(uuid.uuid4())

    def fail() -> None:
        raise RuntimeError("injected before commit")

    with pytest.raises(RuntimeError, match="injected"):
        store.transition(
            created.trip_id,
            created.bearer,
            idempotency_key=key,
            expected_state_version=0,
            requested_state=TripState.ON_FIRST_LEG,
            boarded_identifier="pattern",
            now=103,
            before_commit=fail,
        )
    assert store.authorize_trip(created.trip_id, created.bearer, now=103).state_version == 0
    committed = store.transition(
        created.trip_id,
        created.bearer,
        idempotency_key=key,
        expected_state_version=0,
        requested_state=TripState.ON_FIRST_LEG,
        boarded_identifier="pattern",
        now=103,
    )
    replay = store.transition(
        created.trip_id,
        created.bearer,
        idempotency_key=key,
        expected_state_version=0,
        requested_state=TripState.ON_FIRST_LEG,
        boarded_identifier="pattern",
        now=104,
    )
    assert committed.state_version == 1
    assert replay.idempotent_replay is True
    assert replay.event_sequence == committed.event_sequence
    with pytest.raises(ConflictError, match="another request"):
        store.transition(
            created.trip_id,
            created.bearer,
            idempotency_key=key,
            expected_state_version=1,
            requested_state=TripState.AT_TRANSFER,
            boarded_identifier=None,
            now=104,
        )
    events = store.events_after(created.trip_id, created.bearer, last_sequence=0, now=104)
    assert [event["event_kind"] for event in events] == [
        "TRIP_CREATED",
        "STATE_TRANSITION_ACKNOWLEDGED",
    ]
    store.close()


def test_expiry_cleanup_and_stop_delete_authority() -> None:
    store = CapabilityTripStore(":memory:", _config(decision_ttl_seconds=10, trip_ttl_seconds=20))
    expired = store.issue_decision(_snapshot(), recommended_itinerary_id="a" * 64, now=100)
    with pytest.raises(AuthorizationError):
        store.consume_and_create_trip(
            expired.capability,
            selected_itinerary_id="a" * 64,
            now=110,
        )
    created = _create(store, now=200)
    store.delete_trip(created.trip_id, created.bearer, now=201)
    with pytest.raises(AuthorizationError):
        store.authorize_trip(created.trip_id, created.bearer, now=201)
    store.cleanup(now=1_000)
    assert store._connection.execute("SELECT count(*) FROM decisions").fetchone()[0] == 0
    store.close()


def test_live_update_allow_list_preserves_frozen_initial_cdf() -> None:
    store = CapabilityTripStore(":memory:", _config())
    created = _create(store)
    initial = store.authorize_trip(created.trip_id, created.bearer, now=102).snapshot
    sequence = store.append_live_update(
        created.trip_id,
        created.bearer,
        event_kind=LiveEventKind.OFFICIAL_TRIP_UPDATE,
        event_cutoff_epoch=103,
        source_attempt_lineage=("attempt-tu",),
        freshness_state="FRESH",
        values={"official_arrival_annotation": "2025-01-01T12:30:00Z"},
    )
    assert sequence == 2
    assert store.authorize_trip(created.trip_id, created.bearer, now=103).snapshot == initial
    with pytest.raises(ValueError, match="initial CDF"):
        store.append_live_update(
            created.trip_id,
            created.bearer,
            event_kind=LiveEventKind.ALERT_ELIGIBILITY_CHANGED,
            event_cutoff_epoch=104,
            source_attempt_lineage=("attempt-alert",),
            freshness_state="FRESH",
            values={"deadline_probability": "0.1"},
        )
    with pytest.raises(ValueError, match="supported state"):
        store.append_live_update(
            created.trip_id,
            created.bearer,
            event_kind=LiveEventKind.CONDITIONAL_TRANSFER_ESTIMATE,
            event_cutoff_epoch=104,
            source_attempt_lineage=("attempt-vp",),
            freshness_state="FRESH",
            values={"conditional_transfer_probability": "0.4"},
            conditional_transfer_supported=True,
        )
    event = store.events_after(created.trip_id, created.bearer, last_sequence=1, now=104)[0]
    assert event["value_provenance"] == "OFFICIAL_TRIP_UPDATE"
    assert event["source_attempt_lineage"] == ["attempt-tu"]
    store.close()
