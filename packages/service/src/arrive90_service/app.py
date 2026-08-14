"""Read-only FastAPI adapter for the verified held-out replay explorer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import JSONResponse

from arrive90_service.explorer import (
    DEFAULT_CLAIMS,
    DEFAULT_DEMO_ROOT,
    DEFAULT_FINAL_REPORT,
    ExplorerArtifactError,
    ExplorerRepository,
)

_WEB_ROOT = Path(__file__).with_name("web")
_NO_STORE = {"Cache-Control": "no-store"}


def create_app(
    *,
    repository: ExplorerRepository | None = None,
    demo_root: Path = DEFAULT_DEMO_ROOT,
    final_report_path: Path = DEFAULT_FINAL_REPORT,
    claims_path: Path = DEFAULT_CLAIMS,
) -> FastAPI:
    artifact_error: str | None = None
    if repository is None:
        try:
            repository = ExplorerRepository.load(
                demo_root,
                final_report_path=final_report_path,
                claims_path=claims_path,
            )
        except ExplorerArtifactError as error:
            artifact_error = str(error)

    app = FastAPI(
        title="Arrive90 held-out replay explorer",
        version="travel-time-replay-explorer-v1",
        docs_url=None,
        redoc_url=None,
        openapi_url="/v1/openapi.json",
    )

    def available() -> ExplorerRepository:
        if repository is None:
            raise HTTPException(
                503,
                detail={
                    "reason": "EXPLORER_ARTIFACT_UNAVAILABLE",
                    "message": artifact_error or "explorer artifacts are unavailable",
                },
            )
        return repository

    @app.exception_handler(ExplorerArtifactError)
    async def artifact_failure(_request: Any, error: ExplorerArtifactError) -> JSONResponse:
        return JSONResponse(
            {
                "detail": {
                    "message": str(error),
                    "reason": "EXPLORER_ARTIFACT_UNAVAILABLE",
                }
            },
            status_code=503,
            headers=_NO_STORE,
        )

    @app.get("/v1/system/status")
    def system_status() -> JSONResponse:
        return JSONResponse(
            {
                "artifact_status": "READY" if repository is not None else "UNAVAILABLE",
                "release_mode": "LOOPBACK_LOCAL_REPLAY",
                "status": "READY" if repository is not None else "DEGRADED",
            },
            headers=_NO_STORE,
        )

    @app.get("/v1/explorer/metadata")
    def metadata() -> JSONResponse:
        return JSONResponse(available().metadata(), headers=_NO_STORE)

    @app.get("/v1/explorer/lines")
    def lines() -> JSONResponse:
        return JSONResponse(available().lines(), headers=_NO_STORE)

    @app.get("/v1/explorer/stations")
    def stations() -> JSONResponse:
        return JSONResponse(available().stations(), headers=_NO_STORE)

    @app.get("/v1/explorer/inventory")
    def inventory(
        line_id: str = "Blue",
        direction_id: str | None = None,
        origin_stop_id: str | None = None,
        destination_stop_id: str | None = None,
    ) -> JSONResponse:
        try:
            body = available().inventory(
                line_id=line_id,
                direction_id=direction_id,
                origin_stop_id=origin_stop_id,
                destination_stop_id=destination_stop_id,
            )
        except ValueError as error:
            raise HTTPException(422, detail=str(error)) from error
        return JSONResponse(body, headers=_NO_STORE)

    @app.get("/v1/explorer/replays/{replay_id}/prediction")
    def prediction(replay_id: str, horizon_seconds: int = 900) -> JSONResponse:
        try:
            body = available().prediction(replay_id, horizon_seconds=horizon_seconds)
        except KeyError as error:
            raise HTTPException(404, detail=str(error.args[0])) from error
        except ValueError as error:
            raise HTTPException(422, detail=str(error)) from error
        return JSONResponse(body, headers=_NO_STORE)

    @app.get("/v1/explorer/replays/{replay_id}/outcome")
    def outcome(replay_id: str) -> JSONResponse:
        try:
            body = available().reveal(replay_id)
        except KeyError as error:
            raise HTTPException(404, detail=str(error.args[0])) from error
        return JSONResponse(body, headers=_NO_STORE)

    @app.get("/v1/explorer/reliability")
    def reliability(horizon_seconds: int = 900) -> JSONResponse:
        try:
            body = available().reliability(horizon_seconds=horizon_seconds)
        except ValueError as error:
            raise HTTPException(422, detail=str(error)) from error
        return JSONResponse(body, headers=_NO_STORE)

    @app.get("/v1/explorer/evidence")
    def evidence() -> JSONResponse:
        return JSONResponse(available().evidence(), headers=_NO_STORE)

    frontend = APIRouter()
    frontend.frontend("/", directory=_WEB_ROOT)
    app.include_router(frontend)
    return app
