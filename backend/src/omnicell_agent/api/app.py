"""FastAPI application factory with versioned error envelopes."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Callable
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from omnicell_agent.runs.coordinator import (
    ArtifactNotFoundError,
    ArtifactUploadTooLargeError,
    ConversationNotFoundError,
    ReviewConflictError,
    ReviewNotFoundError,
    RunConflictError,
    RunNotFoundError,
)
from omnicell_agent.runs.event_log import EventRunNotFoundError

from .contracts import ErrorDetail, ErrorEnvelope, ErrorInfo
from .health import ReadinessService
from .routes import router
from .service import ApiResourceNotFoundError, ApiService


def _error_response(
    status_code: int,
    *,
    code: str,
    message: str,
    retryable: bool = False,
    details: list[ErrorDetail] | None = None,
) -> JSONResponse:
    envelope = ErrorEnvelope(
        request_id=uuid4(),
        error=ErrorInfo(
            code=code,
            message=message[:2_000],
            retryable=retryable,
            details=details or [],
        ),
    )
    return JSONResponse(status_code=status_code, content=envelope.model_dump(mode="json"))


def create_app(
    service: ApiService | None = None,
    *,
    readiness_service: ReadinessService | None = None,
    lifespan: Callable[[FastAPI], AbstractAsyncContextManager[None]] | None = None,
) -> FastAPI:
    app = FastAPI(
        title="OmniCell-Agent API",
        version="1.0.0",
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/v1/docs",
        lifespan=lifespan,
    )
    app.state.api_service = service
    app.state.readiness_service = readiness_service
    app.include_router(router)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        del request
        details = [
            ErrorDetail(
                code=str(error.get("type") or "validation_error")[:128],
                message=str(error.get("msg") or "invalid request")[:2_000],
                field=".".join(str(item) for item in error.get("loc", ()))[:256]
                or None,
            )
            for error in exc.errors()[:50]
        ]
        return _error_response(
            422,
            code="request_validation_failed",
            message="请求参数不符合 API 契约",
            details=details,
        )

    @app.exception_handler(StarletteHTTPException)
    async def framework_http_error(request: Request, exc: StarletteHTTPException):
        del request
        code = {
            400: "invalid_request",
            404: "resource_not_found",
            409: "lifecycle_conflict",
            413: "artifact_too_large",
            422: "request_validation_failed",
        }.get(exc.status_code, "http_error")
        return _error_response(
            exc.status_code,
            code=code,
            message=str(exc.detail),
        )

    not_found_errors = (
        ApiResourceNotFoundError,
        ArtifactNotFoundError,
        ConversationNotFoundError,
        EventRunNotFoundError,
        ReviewNotFoundError,
        RunNotFoundError,
    )
    for error_type in not_found_errors:
        app.add_exception_handler(error_type, _not_found_handler)
    for error_type in (ReviewConflictError, RunConflictError):
        app.add_exception_handler(error_type, _conflict_handler)
    app.add_exception_handler(ArtifactUploadTooLargeError, _upload_too_large_handler)

    @app.exception_handler(ValueError)
    async def invalid_value(request: Request, exc: ValueError):
        del request
        return _error_response(400, code="invalid_request", message=str(exc))

    return app


async def _not_found_handler(request: Request, exc: Exception) -> JSONResponse:
    del request
    return _error_response(404, code="resource_not_found", message=str(exc))


async def _conflict_handler(request: Request, exc: Exception) -> JSONResponse:
    del request
    return _error_response(409, code="lifecycle_conflict", message=str(exc))


async def _upload_too_large_handler(request: Request, exc: Exception) -> JSONResponse:
    del request
    return _error_response(413, code="artifact_too_large", message=str(exc))


__all__ = ["create_app"]
