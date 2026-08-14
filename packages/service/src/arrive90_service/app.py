"""FastAPI adapter around the frozen decision and session contracts."""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from arrive90_decision.contracts import InitialDecisionRequest, SelectedItinerary, TripState
from arrive90_decision.initial import select_initial_decision
from arrive90_decision.recovery import select_recovery_decision
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from arrive90_service.contracts import (
    JourneyBackend,
    RecoveryBackend,
    RecoveryRequest,
    ServiceConfig,
)
from arrive90_service.middleware import BoundaryMiddleware, Clock, utc_clock
from arrive90_service.normalization import normalize_initial_request
from arrive90_service.rate_limit import FixedWindowLimiter
from arrive90_service.store import (
    AuthorizationError,
    AuthorizedTrip,
    CapabilityTripStore,
    ConflictError,
    RecoveryBinding,
)

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_NO_STORE = {"Cache-Control": "no-store"}
_AUTH_FAILURE = {"detail": "authorization failed"}


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin_station_id: str = Field(min_length=1, max_length=128)
    destination_station_id: str = Field(min_length=1, max_length=128)
    ready_at: datetime
    deadline: datetime
    reliability_target: Decimal
    maximum_extra_minutes: int = Field(ge=0, le=20)


class TripCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_id: str = Field(min_length=1, max_length=256)
    selected_itinerary_id: str = Field(min_length=1, max_length=128)


class StateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: uuid.UUID
    expected_state_version: int = Field(ge=0)
    next_state: Literal["ON_FIRST_LEG", "AT_TRANSFER", "ON_FINAL_LEG"]
    boarded_itinerary_or_route_pattern_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
    )
    recovery_decision_id: str | None = Field(default=None, min_length=1, max_length=128)


class StopRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: uuid.UUID
    expected_state_version: int = Field(ge=0)


def _require_safe_identifier(value: str) -> None:
    if _SAFE_IDENTIFIER.fullmatch(value) is None:
        raise HTTPException(422, "identifier contains unsupported characters")


def _authorization_header(value: str | None) -> str:
    if value is None or not value.startswith("Bearer ") or len(value) > 256:
        raise HTTPException(401, _AUTH_FAILURE["detail"])
    bearer = value.removeprefix("Bearer ")
    if len(bearer) != 43:
        raise HTTPException(401, _AUTH_FAILURE["detail"])
    return bearer


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _slot(
    selected: Any,
    *,
    transfer_counts: dict[str, int],
) -> dict[str, Any] | None:
    if selected is None:
        return None
    return {
        "arrival_quantiles": {
            level: _timestamp(arrival) for level, arrival in selected.quantile_arrivals
        },
        "deadline_probability": (
            format(selected.deadline_probability, "f")
            if selected.deadline_probability is not None
            else None
        ),
        "extra_planned_time_seconds": selected.extra_planned_time_seconds,
        "itinerary_id": selected.policy_key,
        "model_output_status": selected.model_output_status,
        "planned_time_seconds": selected.planned_time_seconds,
        "transfer_count": transfer_counts[selected.policy_key],
    }


def _client_key(request: Request) -> str:
    client = request.client
    return client.host if client is not None else "unknown"


def _origin_allowed(request: Request, config: ServiceConfig) -> None:
    if request.headers.get("origin") not in config.allowed_origins:
        raise HTTPException(403, "request rejected")


def _bearer_or_failure(
    store: CapabilityTripStore,
    trip_id: str,
    authorization: str | None,
    *,
    now: float,
) -> tuple[str, AuthorizedTrip]:
    bearer = _authorization_header(authorization)
    try:
        return bearer, store.authorize_trip(trip_id, bearer, now=now)
    except AuthorizationError as error:
        raise HTTPException(401, _AUTH_FAILURE["detail"]) from error


def create_app(
    *,
    backend: JourneyBackend,
    store: CapabilityTripStore,
    config: ServiceConfig,
    recovery_backend: RecoveryBackend | None = None,
    clock: Clock = utc_clock,
    epoch_clock: Callable[[], float] = time.time,
) -> FastAPI:
    app = FastAPI(
        title="Arrive90 API",
        version="1.0.0-local",
        docs_url=None,
        redoc_url=None,
        openapi_url="/v1/openapi.json",
    )
    app.add_middleware(BoundaryMiddleware, config=config, clock=clock)
    limiter = FixedWindowLimiter()
    active_streams: set[str] = set()
    active_streams_lock = threading.Lock()
    supported_stations = frozenset(station.station_id for station in backend.stations())

    @app.exception_handler(AuthorizationError)
    async def authorization_failure(_request: Request, _error: AuthorizationError) -> JSONResponse:
        return JSONResponse(_AUTH_FAILURE, status_code=401, headers=_NO_STORE)

    @app.exception_handler(RequestValidationError)
    async def request_validation_failure(
        request: Request, _error: RequestValidationError
    ) -> JSONResponse:
        if request.url.path == "/v1/trips":
            return JSONResponse(_AUTH_FAILURE, status_code=401, headers=_NO_STORE)
        return JSONResponse(
            {"detail": "request invalid"},
            status_code=422,
            headers=_NO_STORE,
        )

    @app.get("/v1/stations")
    def stations() -> dict[str, Any]:
        return {
            "stations": [
                {"station_id": station.station_id, "name": station.name}
                for station in backend.stations()
            ]
        }

    @app.get("/v1/system/status")
    def system_status() -> dict[str, str]:
        return {"release_mode": "LOOPBACK_LOCAL", "status": "READY"}

    @app.get("/v1/models/active")
    def active_models() -> dict[str, str]:
        return {
            "arrival_model": "BACKEND_DECLARED_PER_DECISION",
            "transfer_model": "BACKEND_DECLARED_PER_DECISION",
        }

    @app.get("/v1/methodology")
    def methodology() -> dict[str, str]:
        return {
            "decision_policy": "V1",
            "recovery_policy": "SCHEDULE_ONLY_V1",
            "status": "LOCAL_IMPLEMENTATION_SOURCE_GATE_BLOCKED",
        }

    @app.post("/v1/journeys/search")
    def search(payload: SearchRequest, request: Request) -> JSONResponse:
        _origin_allowed(request, config)
        now_epoch = epoch_clock()
        _require_safe_identifier(payload.origin_station_id)
        _require_safe_identifier(payload.destination_station_id)
        cutoff: datetime = request.state.initial_query_cutoff_utc
        try:
            normalized = normalize_initial_request(
                origin_station_id=payload.origin_station_id,
                destination_station_id=payload.destination_station_id,
                requested_ready_at_utc=payload.ready_at,
                requested_deadline_at_utc=payload.deadline,
                reliability_target=payload.reliability_target,
                maximum_extra_minutes=payload.maximum_extra_minutes,
                initial_query_cutoff_utc=cutoff,
                supported_station_ids=supported_stations,
            )
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        if not limiter.allow(
            "search",
            _client_key(request),
            now=now_epoch,
            limit=config.search_limit_per_minute,
            window_seconds=60,
        ):
            raise HTTPException(429, "rate limit exceeded")
        materials = backend.search(normalized)
        if materials.decision_context.decision_cutoff_utc != cutoff:
            raise RuntimeError("backend changed the server-owned decision cutoff")
        deadline_slack_minutes = int(
            (
                normalized.effective_deadline_at_utc - normalized.effective_ready_at_utc
            ).total_seconds()
            // 60
        )
        decision = select_initial_decision(
            materials.scores,
            request=InitialDecisionRequest(
                normalized.effective_ready_at_utc,
                normalized.effective_deadline_at_utc,
                normalized.reliability_target,
                normalized.maximum_extra_minutes * 60,
                f"slack-{deadline_slack_minutes}",
            ),
            context=materials.decision_context,
            eligibility=materials.eligibility_manifest,
            horizon_support=materials.horizon_support_manifest,
            scoring_state=materials.scoring_state,
        )
        scores_by_key = {score.itinerary.policy_key: score for score in materials.scores}
        transfer_counts = {
            key: score.itinerary.transfer_count for key, score in scores_by_key.items()
        }
        recommendation = _slot(decision.recommendation, transfer_counts=transfer_counts)
        comparator = _slot(decision.comparator, transfer_counts=transfer_counts)
        backup = _slot(decision.backup_itinerary, transfer_counts=transfer_counts)
        alternatives = [
            _slot(
                # The public alternative slot is intentionally reconstructed without model output.
                decision.comparator
                if decision.comparator is not None and decision.comparator.policy_key == key
                else SelectedItinerary(
                    key,
                    int(
                        (
                            scores_by_key[key].itinerary.scheduled_arrival_utc
                            - normalized.effective_ready_at_utc
                        ).total_seconds()
                    ),
                    int(
                        (
                            scores_by_key[key].itinerary.scheduled_arrival_utc
                            - normalized.effective_ready_at_utc
                        ).total_seconds()
                    )
                    - (decision.comparator.planned_time_seconds if decision.comparator else 0),
                    None,
                    None,
                    (),
                    "NOT_SELECTED_OUTPUT_UNVALIDATED",
                ),
                transfer_counts=transfer_counts,
            )
            for key in decision.cap_eligible_policy_keys
            if recommendation is None or key != recommendation["itinerary_id"]
        ]
        model_version = materials.model_version
        support_status = "SUPPORTED"
        if decision.status.value == "DEGRADED_SCHEDULE_ONLY":
            model_version = "STATIC_SCHEDULE_BASELINE_V1"
            support_status = "UNSUPPORTED_READY_HORIZON"
        elif decision.recommendation is None or (
            decision.recommendation.deadline_probability is None
        ):
            support_status = "INSUFFICIENT_EVIDENCE"
        selected_score = (
            scores_by_key[decision.recommendation.policy_key]
            if decision.recommendation is not None
            else None
        )
        selected_itinerary = (
            {
                "allowed_boarding_ids": [
                    selected_score.itinerary.policy_key,
                    *(leg.route_pattern_id for leg in selected_score.itinerary.legs),
                ],
                "itinerary_id": selected_score.itinerary.policy_key,
                "transfer_station_id": (
                    selected_score.itinerary.legs[0].alighting_parent_station_id
                    if selected_score.itinerary.transfer_count == 1
                    else None
                ),
                "transfer_count": selected_score.itinerary.transfer_count,
            }
            if selected_score is not None
            else None
        )
        snapshot: dict[str, Any] = {
            "candidate_generator_version": materials.candidate_generator_version,
            "data_cutoff": _timestamp(cutoff),
            "decision_context_id": materials.decision_context.context_id,
            "decision_context_version": materials.decision_context.context_version,
            "effective_deadline_at": _timestamp(normalized.effective_deadline_at_utc),
            "effective_ready_at": _timestamp(normalized.effective_ready_at_utc),
            "eligibility_mask_hash": materials.decision_context.eligibility_mask_hash,
            "feature_schema_version": materials.feature_schema_version,
            "feed_status": materials.feed_status.value,
            "maximum_extra_minutes": normalized.maximum_extra_minutes,
            "model_version": model_version,
            "reliability_target": format(normalized.reliability_target, ".2f"),
            "requested_deadline_at": _timestamp(normalized.requested_deadline_at_utc),
            "requested_ready_at": _timestamp(normalized.requested_ready_at_utc),
            "selected_itinerary": selected_itinerary,
            "source_attempt_lineage": list(materials.source_attempt_lineage),
            "static_candidate_manifest_hash": materials.decision_context.candidate_manifest_hash,
            "target_status": decision.status.value,
        }
        issued = None
        if (
            decision.trip_start_supported
            and selected_itinerary is not None
            and selected_score is not None
        ):
            issued = store.issue_decision(
                snapshot,
                recommended_itinerary_id=selected_score.itinerary.policy_key,
                now=now_epoch,
            )
        public_snapshot = {
            key: value for key, value in snapshot.items() if key != "selected_itinerary"
        }
        response = {
            **public_snapshot,
            "alternatives": alternatives,
            "backup_itinerary": backup,
            "deadline_time_status": normalized.deadline_time_status,
            "decision_expires_at": issued.expires_at_epoch if issued is not None else None,
            "decision_id": issued.capability if issued is not None else None,
            "explanation_codes": list(decision.explanation_codes),
            "fastest_itinerary": comparator,
            "limitations": list(normalized.limitations),
            "ready_time_status": normalized.ready_time_status,
            "recommended_itinerary": recommendation,
            "request_id": str(uuid.uuid4()),
            "support_status": support_status,
            "trip_start_supported": issued is not None,
        }
        return JSONResponse(response, headers=_NO_STORE)

    @app.post("/v1/trips")
    def create_trip(payload: TripCreateRequest, request: Request) -> JSONResponse:
        _origin_allowed(request, config)
        now = epoch_clock()
        if not limiter.allow(
            "trip-create",
            _client_key(request),
            now=now,
            limit=config.trip_creation_limit_per_hour,
            window_seconds=3_600,
        ):
            raise HTTPException(429, "rate limit exceeded")
        try:
            created = store.consume_and_create_trip(
                payload.decision_id,
                selected_itinerary_id=payload.selected_itinerary_id,
                now=now,
            )
        except AuthorizationError as error:
            raise HTTPException(401, _AUTH_FAILURE["detail"]) from error
        return JSONResponse(
            {
                "expires_at": created.expires_at_epoch,
                "state": TripState.NOT_STARTED.value,
                "state_version": 0,
                "trip_bearer": created.bearer,
                "trip_id": created.trip_id,
            },
            headers=_NO_STORE,
        )

    @app.get("/v1/trips/{trip_id}")
    def get_trip(
        trip_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> JSONResponse:
        bearer, trip = _bearer_or_failure(store, trip_id, authorization, now=epoch_clock())
        del bearer
        return JSONResponse(
            {
                "initial_decision": {
                    key: value
                    for key, value in trip.snapshot.items()
                    if key != "selected_itinerary"
                },
                "state": trip.state.value,
                "state_version": trip.state_version,
                "trip_id": trip.trip_id,
            },
            headers=_NO_STORE,
        )

    @app.post("/v1/trips/{trip_id}/state")
    def update_state(
        trip_id: str,
        payload: StateRequest,
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
    ) -> JSONResponse:
        _origin_allowed(request, config)
        bearer = _authorization_header(authorization)
        now = epoch_clock()
        if not limiter.allow(
            "trip-state",
            trip_id,
            now=now,
            limit=config.state_limit_per_minute,
            window_seconds=60,
        ):
            raise HTTPException(429, "rate limit exceeded")
        try:
            result = store.transition(
                trip_id,
                bearer,
                idempotency_key=str(payload.idempotency_key),
                expected_state_version=payload.expected_state_version,
                requested_state=TripState(payload.next_state),
                boarded_identifier=payload.boarded_itinerary_or_route_pattern_id,
                now=now,
                recovery_decision_id=payload.recovery_decision_id,
            )
        except AuthorizationError as error:
            raise HTTPException(401, _AUTH_FAILURE["detail"]) from error
        except (ConflictError, ValueError) as error:
            raise HTTPException(409, "state transition conflict") from error
        response: dict[str, Any] = {
            "event_sequence": result.event_sequence,
            "idempotent_replay": result.idempotent_replay,
            "recovery_decision": None,
            "state": result.state.value,
            "state_version": result.state_version,
            "trip_id": result.trip_id,
        }
        if (
            not result.idempotent_replay
            and result.state is TripState.AT_TRANSFER
            and recovery_backend is not None
        ):
            authorized = store.authorize_trip(trip_id, bearer, now=now)
            current_station = authorized.snapshot["selected_itinerary"]["transfer_station_id"]
            if not isinstance(current_station, str):
                raise RuntimeError("transfer state lacks a bound transfer station")
            recovery_cutoff: datetime = request.state.initial_query_cutoff_utc
            recovery_materials = recovery_backend.recovery(
                RecoveryRequest(
                    trip_id,
                    current_station,
                    result.state_version,
                    recovery_cutoff,
                    authorized.snapshot,
                )
            )
            if recovery_materials.decision_context.decision_cutoff_utc != recovery_cutoff:
                raise RuntimeError("backend changed the server-owned recovery cutoff")
            recovery = select_recovery_decision(
                recovery_materials.candidates,
                continuation_policy_key=recovery_materials.continuation_policy_key,
                context=recovery_materials.decision_context,
                trigger=recovery_materials.trigger,
            )
            if recovery is not None and recovery.recommendation is not None:
                recovery_counts = {
                    candidate.policy_key: candidate.transfer_count
                    for candidate in recovery_materials.candidates
                }
                recovery_payload = {
                    "backup_itinerary": _slot(
                        recovery.backup_itinerary,
                        transfer_counts=recovery_counts,
                    ),
                    "cap_reference": _slot(
                        recovery.cap_reference,
                        transfer_counts=recovery_counts,
                    ),
                    "continuation_comparator": _slot(
                        recovery.continuation_comparator,
                        transfer_counts=recovery_counts,
                    ),
                    "deadline_probability": None,
                    "new_arrival_quantiles": None,
                    "reason": recovery.winning_reason.value,
                    "reasons": [item.value for item in recovery.reasons],
                    "recommendation": _slot(
                        recovery.recommendation,
                        transfer_counts=recovery_counts,
                    ),
                    "recovery_status": recovery.status.value,
                }
                candidates_by_key = {
                    candidate.policy_key: candidate for candidate in recovery_materials.candidates
                }
                bindings = tuple(
                    RecoveryBinding(
                        key,
                        candidates_by_key[key].transfer_count,
                        (
                            key,
                            *(leg.route_pattern_id for leg in candidates_by_key[key].legs),
                        ),
                    )
                    for key in recovery.selectable_policy_keys
                )
                issued_recovery = store.issue_recovery(
                    trip_id,
                    bearer,
                    expected_state_version=result.state_version,
                    current_station_id=current_station,
                    decision_payload=recovery_payload,
                    bindings=bindings,
                    now=now,
                )
                response["recovery_decision"] = {
                    **recovery_payload,
                    "recovery_decision_expires_at": issued_recovery.expires_at_epoch,
                    "recovery_decision_id": issued_recovery.recovery_decision_id,
                }
        return JSONResponse(response, headers=_NO_STORE)

    @app.post("/v1/trips/{trip_id}/stop")
    def stop_trip(
        trip_id: str,
        payload: StopRequest,
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
    ) -> JSONResponse:
        _origin_allowed(request, config)
        bearer = _authorization_header(authorization)
        try:
            result = store.transition(
                trip_id,
                bearer,
                idempotency_key=str(payload.idempotency_key),
                expected_state_version=payload.expected_state_version,
                requested_state=TripState.ENDED,
                boarded_identifier=None,
                now=epoch_clock(),
                stop_requested=True,
            )
            store.delete_trip(trip_id, bearer, now=epoch_clock())
        except AuthorizationError as error:
            raise HTTPException(401, _AUTH_FAILURE["detail"]) from error
        except (ConflictError, ValueError) as error:
            raise HTTPException(409, "state transition conflict") from error
        return JSONResponse(
            {"state": result.state.value, "state_version": result.state_version},
            headers=_NO_STORE,
        )

    @app.get("/v1/trips/{trip_id}/events")
    def events(
        trip_id: str,
        authorization: Annotated[str | None, Header()] = None,
        last_event_id: Annotated[int | None, Header(alias="Last-Event-ID")] = None,
    ) -> StreamingResponse:
        bearer, _trip = _bearer_or_failure(store, trip_id, authorization, now=epoch_clock())
        with active_streams_lock:
            if trip_id in active_streams:
                raise HTTPException(429, "one active stream is allowed per trip")
            active_streams.add(trip_id)

        def stream() -> Iterator[bytes]:
            try:
                retained = store.events_after(
                    trip_id,
                    bearer,
                    last_sequence=last_event_id or 0,
                    now=epoch_clock(),
                )
                for event in retained:
                    data = json.dumps(event, sort_keys=True, separators=(",", ":"))
                    event_text = (
                        f"id: {event['sequence']}\nevent: {event['event_kind']}\ndata: {data}\n\n"
                    )
                    yield event_text.encode()
            finally:
                with active_streams_lock:
                    active_streams.discard(trip_id)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={**_NO_STORE, "X-Accel-Buffering": "no"},
        )

    return app
