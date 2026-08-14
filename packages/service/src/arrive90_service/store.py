"""SQLite capability, trip, idempotency, and bounded SSE outbox store."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from arrive90_decision.contracts import TripState
from arrive90_decision.recovery import next_trip_state

from arrive90_service.contracts import LiveEventKind, ServiceConfig

_DUMMY_DIGEST = bytes(32)


def _token() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")


def _digest(secret: bytes, token: str) -> bytes:
    return hmac.digest(secret, token.encode("ascii", errors="ignore"), "sha256")


def _canonical(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


@dataclass(frozen=True)
class IssuedDecision:
    capability: str
    expires_at_epoch: float


@dataclass(frozen=True)
class CreatedTrip:
    trip_id: str
    bearer: str
    expires_at_epoch: float
    snapshot: dict[str, Any]


@dataclass(frozen=True)
class AuthorizedTrip:
    trip_id: str
    state: TripState
    state_version: int
    active_itinerary_id: str
    transfer_count: int
    active_allowed_boarding_ids: tuple[str, ...]
    snapshot: dict[str, Any]


@dataclass(frozen=True)
class TransitionResult:
    trip_id: str
    state: TripState
    state_version: int
    idempotent_replay: bool
    event_sequence: int


@dataclass(frozen=True)
class RecoveryBinding:
    itinerary_id: str
    transfer_count: int
    allowed_boarding_ids: tuple[str, ...]


@dataclass(frozen=True)
class IssuedRecovery:
    recovery_decision_id: str
    expires_at_epoch: float
    event_sequence: int


class AuthorizationError(Exception):
    pass


class ConflictError(Exception):
    pass


class CapabilityTripStore:
    def __init__(self, path: Path | str, config: ServiceConfig) -> None:
        self._config = config
        self._decision_keys = dict(config.decision_keys)
        self._trip_keys = dict(config.trip_keys)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        self._initialize()

    def close(self) -> None:
        self._connection.close()

    def _initialize(self) -> None:
        self._connection.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE IF NOT EXISTS decisions (
                digest BLOB PRIMARY KEY,
                key_version TEXT NOT NULL,
                expires_at REAL NOT NULL,
                consumed_at REAL,
                recommended_itinerary_id TEXT NOT NULL,
                snapshot_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS trips (
                trip_id TEXT PRIMARY KEY,
                bearer_digest BLOB NOT NULL,
                key_version TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                state TEXT NOT NULL,
                state_version INTEGER NOT NULL,
                active_itinerary_id TEXT NOT NULL,
                transfer_count INTEGER NOT NULL,
                active_allowed_ids_json TEXT NOT NULL,
                snapshot_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS idempotency (
                trip_id TEXT NOT NULL REFERENCES trips(trip_id) ON DELETE CASCADE,
                idempotency_key TEXT NOT NULL,
                request_digest TEXT NOT NULL,
                response_json TEXT NOT NULL,
                PRIMARY KEY (trip_id, idempotency_key)
            );
            CREATE TABLE IF NOT EXISTS events (
                trip_id TEXT NOT NULL REFERENCES trips(trip_id) ON DELETE CASCADE,
                sequence INTEGER NOT NULL,
                created_at REAL NOT NULL,
                payload_json TEXT NOT NULL,
                payload_bytes INTEGER NOT NULL,
                PRIMARY KEY (trip_id, sequence)
            );
            CREATE TABLE IF NOT EXISTS recovery_decisions (
                recovery_decision_id TEXT PRIMARY KEY,
                trip_id TEXT NOT NULL REFERENCES trips(trip_id) ON DELETE CASCADE,
                expected_state_version INTEGER NOT NULL,
                current_station_id TEXT NOT NULL,
                expires_at REAL NOT NULL,
                consumed_at REAL,
                decision_payload_json TEXT NOT NULL,
                bindings_json TEXT NOT NULL
            );
            """
        )

    def issue_decision(
        self,
        snapshot: dict[str, Any],
        *,
        recommended_itinerary_id: str,
        now: float,
    ) -> IssuedDecision:
        token = _token()
        version = self._config.active_decision_key_version
        digest = _digest(self._decision_keys[version], token)
        expires = now + self._config.decision_ttl_seconds
        with self._lock:
            self._connection.execute(
                "INSERT INTO decisions VALUES (?, ?, ?, NULL, ?, ?)",
                (digest, version, expires, recommended_itinerary_id, _canonical(snapshot)),
            )
        return IssuedDecision(token, expires)

    def consume_and_create_trip(
        self,
        capability: str,
        *,
        selected_itinerary_id: str,
        now: float,
    ) -> CreatedTrip:
        candidate_digests = {
            version: _digest(secret, capability) for version, secret in self._decision_keys.items()
        }
        trip_id = str(uuid.uuid4())
        bearer = _token()
        trip_version = self._config.active_trip_key_version
        bearer_digest = _digest(self._trip_keys[trip_version], bearer)
        trip_expires = now + self._config.trip_ttl_seconds
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row: sqlite3.Row | None = None
                matched_digest = _DUMMY_DIGEST
                comparison_digest = _DUMMY_DIGEST
                for version, candidate in candidate_digests.items():
                    possible = self._connection.execute(
                        "SELECT * FROM decisions WHERE digest = ? AND key_version = ?",
                        (candidate, version),
                    ).fetchone()
                    if possible is not None:
                        row = possible
                        matched_digest = bytes(possible["digest"])
                        comparison_digest = candidate
                        break
                digest_ok = hmac.compare_digest(matched_digest, comparison_digest)
                valid = (
                    row is not None
                    and digest_ok
                    and row["consumed_at"] is None
                    and float(row["expires_at"]) > now
                    and hmac.compare_digest(
                        str(row["recommended_itinerary_id"]), selected_itinerary_id
                    )
                )
                if not valid or row is None:
                    if row is not None and (
                        row["consumed_at"] is not None or float(row["expires_at"]) <= now
                    ):
                        self._connection.execute(
                            "DELETE FROM decisions WHERE digest = ?", (row["digest"],)
                        )
                    raise AuthorizationError
                snapshot = json.loads(str(row["snapshot_json"]))
                allowed = snapshot["selected_itinerary"]
                self._connection.execute(
                    "INSERT INTO trips VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)",
                    (
                        trip_id,
                        bearer_digest,
                        trip_version,
                        now,
                        trip_expires,
                        TripState.NOT_STARTED.value,
                        selected_itinerary_id,
                        int(allowed["transfer_count"]),
                        _canonical({"ids": allowed["allowed_boarding_ids"]}),
                        _canonical(snapshot),
                    ),
                )
                self._append_event_locked(
                    trip_id,
                    now=now,
                    payload=self._event_payload(snapshot, "TRIP_CREATED", now, state_version=0),
                )
                self._connection.execute(
                    "DELETE FROM decisions WHERE digest = ?",
                    (row["digest"],),
                )
                self._connection.execute("COMMIT")
            except AuthorizationError:
                self._connection.execute("COMMIT")
                raise
            except Exception:
                self._connection.execute("ROLLBACK")
                raise
        return CreatedTrip(trip_id, bearer, trip_expires, snapshot)

    def authorize_trip(self, trip_id: str, bearer: str, *, now: float) -> AuthorizedTrip:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM trips WHERE trip_id = ?",
                (trip_id,),
            ).fetchone()
            if row is not None and float(row["expires_at"]) <= now:
                self._connection.execute("DELETE FROM trips WHERE trip_id = ?", (trip_id,))
                row = None
            if row is None:
                hmac.compare_digest(_DUMMY_DIGEST, _DUMMY_DIGEST)
                raise AuthorizationError
            version = str(row["key_version"])
            secret = self._trip_keys.get(version, bytes(32))
            candidate = _digest(secret, bearer)
            if not hmac.compare_digest(bytes(row["bearer_digest"]), candidate):
                raise AuthorizationError
            return AuthorizedTrip(
                str(row["trip_id"]),
                TripState(str(row["state"])),
                int(row["state_version"]),
                str(row["active_itinerary_id"]),
                int(row["transfer_count"]),
                tuple(json.loads(str(row["active_allowed_ids_json"]))["ids"]),
                json.loads(str(row["snapshot_json"])),
            )

    def issue_recovery(
        self,
        trip_id: str,
        bearer: str,
        *,
        expected_state_version: int,
        current_station_id: str,
        decision_payload: dict[str, Any],
        bindings: tuple[RecoveryBinding, ...],
        now: float,
    ) -> IssuedRecovery:
        if not bindings or len({item.itinerary_id for item in bindings}) != len(bindings):
            raise ValueError("recovery bindings must be nonempty and unique")
        recovery_id = str(uuid.uuid4())
        expires = now + self._config.decision_ttl_seconds
        serialized_bindings = [
            {
                "allowed_boarding_ids": list(item.allowed_boarding_ids),
                "itinerary_id": item.itinerary_id,
                "transfer_count": item.transfer_count,
            }
            for item in bindings
        ]
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                trip = self.authorize_trip(trip_id, bearer, now=now)
                if trip.state is not TripState.AT_TRANSFER or (
                    trip.state_version != expected_state_version
                ):
                    raise ConflictError("recovery decision state is stale")
                self._connection.execute(
                    "INSERT INTO recovery_decisions VALUES (?, ?, ?, ?, ?, NULL, ?, ?)",
                    (
                        recovery_id,
                        trip_id,
                        expected_state_version,
                        current_station_id,
                        expires,
                        _canonical(decision_payload),
                        json.dumps(serialized_bindings, sort_keys=True, separators=(",", ":")),
                    ),
                )
                event = self._event_payload(
                    trip.snapshot,
                    "RECOVERY_DECISION",
                    now,
                    recovery_decision={
                        **decision_payload,
                        "recovery_decision_expires_at": expires,
                        "recovery_decision_id": recovery_id,
                    },
                )
                event["value_provenance"] = "RECOVERY_SCHEDULE_ONLY"
                sequence = self._append_event_locked(trip_id, now=now, payload=event)
                self._connection.execute("COMMIT")
            except Exception:
                self._connection.execute("ROLLBACK")
                raise
        return IssuedRecovery(recovery_id, expires, sequence)

    def transition(
        self,
        trip_id: str,
        bearer: str,
        *,
        idempotency_key: str,
        expected_state_version: int,
        requested_state: TripState,
        boarded_identifier: str | None,
        now: float,
        stop_requested: bool = False,
        recovery_decision_id: str | None = None,
        before_commit: Callable[[], None] | None = None,
    ) -> TransitionResult:
        request = {
            "boarded_identifier": boarded_identifier,
            "expected_state_version": expected_state_version,
            "recovery_decision_id": recovery_decision_id,
            "requested_state": requested_state.value,
            "stop_requested": stop_requested,
        }
        request_digest = hashlib.sha256(_canonical(request).encode()).hexdigest()
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                trip = self.authorize_trip(trip_id, bearer, now=now)
                prior = self._connection.execute(
                    "SELECT request_digest, response_json FROM idempotency "
                    "WHERE trip_id = ? AND idempotency_key = ?",
                    (trip_id, idempotency_key),
                ).fetchone()
                if prior is not None:
                    if not hmac.compare_digest(str(prior["request_digest"]), request_digest):
                        raise ConflictError("idempotency key was used for another request")
                    response = json.loads(str(prior["response_json"]))
                    self._connection.execute("COMMIT")
                    return TransitionResult(
                        trip_id,
                        TripState(response["state"]),
                        int(response["state_version"]),
                        True,
                        int(response["event_sequence"]),
                    )
                if trip.state_version != expected_state_version:
                    raise ConflictError("state version is stale")
                activating_transfer_count: int | None = None
                active_itinerary_id = trip.active_itinerary_id
                active_allowed_ids = trip.active_allowed_boarding_ids
                recovery_row: sqlite3.Row | None = None
                if recovery_decision_id is not None:
                    recovery_row = self._connection.execute(
                        "SELECT * FROM recovery_decisions WHERE recovery_decision_id = ?",
                        (recovery_decision_id,),
                    ).fetchone()
                    if (
                        recovery_row is None
                        or str(recovery_row["trip_id"]) != trip_id
                        or int(recovery_row["expected_state_version"]) != trip.state_version
                        or recovery_row["consumed_at"] is not None
                        or float(recovery_row["expires_at"]) <= now
                        or trip.state is not TripState.AT_TRANSFER
                    ):
                        raise ConflictError("recovery decision is invalid")
                    bindings = json.loads(str(recovery_row["bindings_json"]))
                    matches = [
                        item
                        for item in bindings
                        if boarded_identifier == item["itinerary_id"]
                        or boarded_identifier in item["allowed_boarding_ids"]
                    ]
                    if len(matches) != 1:
                        raise ConflictError("recovery itinerary is invalid")
                    binding = matches[0]
                    active_itinerary_id = str(binding["itinerary_id"])
                    activating_transfer_count = int(binding["transfer_count"])
                    active_allowed_ids = tuple(binding["allowed_boarding_ids"])
                allowed_ids = set(active_allowed_ids)
                if recovery_row is not None:
                    allowed_ids = set(active_allowed_ids)
                if requested_state in {TripState.ON_FIRST_LEG, TripState.ON_FINAL_LEG} and (
                    boarded_identifier is None or boarded_identifier not in allowed_ids
                ):
                    raise ConflictError("boarded itinerary or route pattern is invalid")
                next_state = next_trip_state(
                    trip.state,
                    requested_state,
                    active_transfer_count=trip.transfer_count,
                    activating_recovery_transfer_count=activating_transfer_count,
                    stop_requested=stop_requested,
                )
                next_version = trip.state_version + 1
                cursor = self._connection.execute(
                    "UPDATE trips SET state = ?, state_version = ?, active_itinerary_id = ?, "
                    "transfer_count = ?, active_allowed_ids_json = ? "
                    "WHERE trip_id = ? AND state_version = ?",
                    (
                        next_state.value,
                        next_version,
                        active_itinerary_id,
                        activating_transfer_count
                        if activating_transfer_count is not None
                        else trip.transfer_count,
                        _canonical({"ids": active_allowed_ids}),
                        trip_id,
                        trip.state_version,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ConflictError("state version is stale")
                if recovery_row is not None:
                    self._connection.execute(
                        "UPDATE recovery_decisions SET consumed_at = ? "
                        "WHERE recovery_decision_id = ? AND consumed_at IS NULL",
                        (now, recovery_decision_id),
                    )
                sequence = self._append_event_locked(
                    trip_id,
                    now=now,
                    payload=self._event_payload(
                        trip.snapshot,
                        "STATE_TRANSITION_ACKNOWLEDGED",
                        now,
                        state=next_state.value,
                        state_version=next_version,
                    ),
                )
                response = {
                    "event_sequence": sequence,
                    "state": next_state.value,
                    "state_version": next_version,
                }
                self._connection.execute(
                    "INSERT INTO idempotency VALUES (?, ?, ?, ?)",
                    (trip_id, idempotency_key, request_digest, _canonical(response)),
                )
                if before_commit is not None:
                    before_commit()
                self._connection.execute("COMMIT")
                return TransitionResult(trip_id, next_state, next_version, False, sequence)
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def events_after(
        self, trip_id: str, bearer: str, *, last_sequence: int, now: float
    ) -> tuple[dict[str, Any], ...]:
        self.authorize_trip(trip_id, bearer, now=now)
        with self._lock:
            self._purge_events_locked(trip_id, now)
            rows = self._connection.execute(
                "SELECT payload_json FROM events WHERE trip_id = ? AND sequence > ? "
                "ORDER BY sequence",
                (trip_id, last_sequence),
            ).fetchall()
        return tuple(json.loads(str(row["payload_json"])) for row in rows)

    def append_live_update(
        self,
        trip_id: str,
        bearer: str,
        *,
        event_kind: LiveEventKind,
        event_cutoff_epoch: float,
        source_attempt_lineage: tuple[str, ...],
        freshness_state: str,
        values: dict[str, Any],
        conditional_transfer_supported: bool = False,
    ) -> int:
        forbidden = {"deadline_probability", "arrival_quantiles", "target_status"}
        if forbidden & values.keys():
            raise ValueError("post-start events cannot revise the initial CDF outputs")
        trip = self.authorize_trip(trip_id, bearer, now=event_cutoff_epoch)
        provenance = {
            LiveEventKind.FEED_FRESHNESS_CHANGED: "DETERMINISTIC_FEED_STATE",
            LiveEventKind.OFFICIAL_TRIP_UPDATE: "OFFICIAL_TRIP_UPDATE",
            LiveEventKind.ALERT_ELIGIBILITY_CHANGED: "DETERMINISTIC_ALERT_STATE",
            LiveEventKind.ORIGINAL_POLICY_UNSUPPORTED: "DETERMINISTIC_ALERT_STATE",
            LiveEventKind.CONDITIONAL_TRANSFER_ESTIMATE: "CONDITIONAL_TRANSFER_MODEL",
        }[event_kind]
        if event_kind is LiveEventKind.CONDITIONAL_TRANSFER_ESTIMATE and (
            trip.state is not TripState.AT_TRANSFER or not conditional_transfer_supported
        ):
            raise ValueError("conditional transfer output is outside its supported state")
        payload = self._event_payload(
            trip.snapshot,
            event_kind.value,
            event_cutoff_epoch,
            **values,
        )
        payload["source_attempt_lineage"] = list(source_attempt_lineage)
        payload["feed_status"] = freshness_state
        payload["value_provenance"] = provenance
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                sequence = self._append_event_locked(
                    trip_id,
                    now=event_cutoff_epoch,
                    payload=payload,
                )
                self._connection.execute("COMMIT")
            except Exception:
                self._connection.execute("ROLLBACK")
                raise
        return sequence

    def delete_trip(self, trip_id: str, bearer: str, *, now: float) -> None:
        self.authorize_trip(trip_id, bearer, now=now)
        with self._lock:
            self._connection.execute("DELETE FROM trips WHERE trip_id = ?", (trip_id,))

    def cleanup(self, *, now: float) -> None:
        with self._lock:
            self._connection.execute("DELETE FROM decisions WHERE expires_at <= ?", (now,))
            self._connection.execute("DELETE FROM trips WHERE expires_at <= ?", (now,))

    def _append_event_locked(self, trip_id: str, *, now: float, payload: dict[str, Any]) -> int:
        row = self._connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence FROM events WHERE trip_id = ?",
            (trip_id,),
        ).fetchone()
        sequence = int(row["next_sequence"])
        payload["sequence"] = sequence
        encoded = _canonical(payload)
        if len(encoded.encode()) > self._config.maximum_sse_bytes:
            raise ValueError("one SSE event exceeds the retained byte bound")
        self._connection.execute(
            "INSERT INTO events VALUES (?, ?, ?, ?, ?)",
            (trip_id, sequence, now, encoded, len(encoded.encode())),
        )
        self._purge_events_locked(trip_id, now)
        return sequence

    def _purge_events_locked(self, trip_id: str, now: float) -> None:
        self._connection.execute(
            "DELETE FROM events WHERE trip_id = ? AND created_at < ?",
            (trip_id, now - self._config.maximum_sse_age_seconds),
        )
        rows = self._connection.execute(
            "SELECT sequence, payload_bytes FROM events WHERE trip_id = ? ORDER BY sequence DESC",
            (trip_id,),
        ).fetchall()
        retained_bytes = 0
        keep: list[int] = []
        for row in rows:
            size = int(row["payload_bytes"])
            if len(keep) >= self._config.maximum_sse_events:
                continue
            if retained_bytes + size > self._config.maximum_sse_bytes:
                continue
            keep.append(int(row["sequence"]))
            retained_bytes += size
        if rows and keep:
            floor = min(keep)
            self._connection.execute(
                "DELETE FROM events WHERE trip_id = ? AND sequence < ?",
                (trip_id, floor),
            )

    @staticmethod
    def _event_payload(
        snapshot: dict[str, Any],
        event_kind: str,
        cutoff_epoch: float,
        **values: Any,
    ) -> dict[str, Any]:
        return {
            "candidate_generator_version": snapshot["candidate_generator_version"],
            "data_cutoff": snapshot["data_cutoff"],
            "decision_context_id": snapshot["decision_context_id"],
            "event_cutoff_epoch": cutoff_epoch,
            "event_kind": event_kind,
            "feature_schema_version": snapshot["feature_schema_version"],
            "feed_status": snapshot["feed_status"],
            "model_version": snapshot["model_version"],
            "source_attempt_lineage": snapshot["source_attempt_lineage"],
            "static_candidate_manifest_hash": snapshot["static_candidate_manifest_hash"],
            "value_provenance": "DETERMINISTIC_STATE",
            **values,
        }
