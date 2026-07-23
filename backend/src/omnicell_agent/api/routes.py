"""REST and SSE routes for API v1."""

from __future__ import annotations

from typing import Annotated, BinaryIO
from urllib.parse import quote
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from omnicell_agent.runs.status import ReviewStatus

from .contracts import (
    ArtifactListResponse,
    ArtifactRead,
    ConversationCreateRequest,
    ConversationListResponse,
    ConversationRead,
    ConversationStatus,
    ErrorEnvelope,
    EventReplayResponse,
    LivenessResponse,
    ReadinessResponse,
    ReviewDecisionRequest,
    ReviewDecisionResponse,
    ReviewListResponse,
    RunCancelRequest,
    RunCancelResponse,
    RunCreateRequest,
    RunCreateResponse,
    RunHistoryResponse,
    RunRead,
    RunResumeRequest,
    RunResumeResponse,
)
from .health import ReadinessService, unavailable_readiness
from .service import ApiService
from .sse import resolve_sse_cursor, sse_frames


def get_api_service(request: Request) -> ApiService:
    service = getattr(request.app.state, "api_service", None)
    if service is None:
        raise RuntimeError("API service 尚未启动")
    return service


Service = Annotated[ApiService, Depends(get_api_service)]


def get_readiness_service(request: Request) -> ReadinessService | None:
    return getattr(request.app.state, "readiness_service", None)


Readiness = Annotated[ReadinessService | None, Depends(get_readiness_service)]

_ERROR_DESCRIPTIONS = {
    400: "请求语义或游标非法。",
    404: "请求的资源不存在。",
    409: "请求与当前生命周期状态冲突。",
    413: "上传内容超过服务端上限。",
    422: "请求参数不符合 API 契约。",
}


def _error_responses(*status_codes: int) -> dict[int, dict[str, object]]:
    return {
        status_code: {
            "model": ErrorEnvelope,
            "description": _ERROR_DESCRIPTIONS[status_code],
        }
        for status_code in status_codes
    }


# 每个入口都可能在 path、query、header 或 body 解析阶段返回 422；显式声明
# 可以覆盖 FastAPI 默认的 HTTPValidationError schema，与运行时 envelope 保持一致。
router = APIRouter(prefix="/api/v1", responses=_error_responses(422))


def _artifact_chunks(stream: BinaryIO, chunk_size: int = 1024 * 1024):
    try:
        while chunk := stream.read(chunk_size):
            yield chunk
    finally:
        stream.close()


@router.get(
    "/health/live",
    response_model=LivenessResponse,
    operation_id="getLiveness",
)
async def get_liveness() -> LivenessResponse:
    return LivenessResponse()


@router.get(
    "/health/ready",
    response_model=ReadinessResponse,
    operation_id="getReadiness",
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ReadinessResponse,
            "description": "至少一个必要依赖尚未就绪。",
        }
    },
)
async def get_readiness(
    response: Response,
    readiness: Readiness,
) -> ReadinessResponse:
    result = unavailable_readiness() if readiness is None else await readiness.check()
    if not result.ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return result


@router.post(
    "/conversations",
    response_model=ConversationRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createConversation",
)
async def create_conversation(body: ConversationCreateRequest, service: Service):
    return await service.create_conversation(title=body.title)


@router.get(
    "/conversations",
    response_model=ConversationListResponse,
    operation_id="listConversations",
    responses=_error_responses(400),
)
async def list_conversations(
    service: Service,
    cursor: str | None = Query(default=None, max_length=2_048),
    limit: int = Query(default=50, ge=1, le=100),
    conversation_status: ConversationStatus | None = Query(default=None, alias="status"),
):
    return await service.list_conversations(
        cursor=cursor,
        limit=limit,
        status=conversation_status,
    )


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationRead,
    operation_id="getConversation",
    responses=_error_responses(404),
)
async def get_conversation(conversation_id: UUID, service: Service):
    return await service.get_conversation(conversation_id)


@router.get(
    "/conversations/{conversation_id}/history",
    response_model=RunHistoryResponse,
    operation_id="getConversationHistory",
    responses=_error_responses(400, 404),
)
async def get_history(
    conversation_id: UUID,
    service: Service,
    cursor: str | None = Query(default=None, max_length=2_048),
    limit: int = Query(default=50, ge=1, le=100),
):
    return await service.history(conversation_id, cursor=cursor, limit=limit)


@router.post(
    "/conversations/{conversation_id}/runs",
    response_model=RunCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="createRun",
    responses=_error_responses(400, 404, 409),
)
async def create_run(
    conversation_id: UUID,
    body: RunCreateRequest,
    service: Service,
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", max_length=255),
    ] = None,
):
    if body.request_key and idempotency_key and body.request_key != idempotency_key:
        raise ValueError("body request_key 与 Idempotency-Key 不一致")
    return await service.create_run(
        conversation_id=conversation_id,
        goal=body.goal,
        input_artifact_ids=body.input_artifact_ids,
        request_key=body.request_key or idempotency_key,
    )


@router.get(
    "/runs/{run_id}",
    response_model=RunRead,
    operation_id="getRun",
    responses=_error_responses(404),
)
async def get_run(run_id: UUID, service: Service):
    return await service.get_run(run_id)


@router.get(
    "/runs/{run_id}/events",
    response_model=EventReplayResponse,
    operation_id="replayRunEvents",
    responses=_error_responses(404),
)
async def replay_events(
    run_id: UUID,
    service: Service,
    after_sequence: str = Query(default="0", pattern=r"^(0|[1-9][0-9]{0,18})$"),
    limit: int = Query(default=200, ge=1, le=500),
):
    return await service.replay(
        run_id,
        after_sequence=int(after_sequence),
        limit=limit,
    )


@router.get(
    "/runs/{run_id}/events/stream",
    response_class=StreamingResponse,
    operation_id="streamRunEvents",
    responses={
        200: {
            "description": "Replay-first run event stream; disconnect does not cancel the run.",
            "content": {
                "text/event-stream": {
                    "example": "id: 1\nevent: run.created\ndata: {...}\n\n"
                }
            },
        },
        **_error_responses(400, 404),
    },
)
async def stream_events(
    run_id: UUID,
    service: Service,
    after_sequence: str | None = Query(
        default=None,
        pattern=r"^(0|[1-9][0-9]{0,18})$",
    ),
    last_event_id: Annotated[
        str | None,
        Header(alias="Last-Event-ID", pattern=r"^(0|[1-9][0-9]{0,18})$"),
    ] = None,
):
    cursor = resolve_sse_cursor(after_sequence, last_event_id)
    # Fail before headers are sent if the run does not exist.
    await service.get_run(run_id)
    return StreamingResponse(
        sse_frames(service.event_log, run_id, after_sequence=cursor),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post(
    "/runs/{run_id}/cancel",
    response_model=RunCancelResponse,
    operation_id="cancelRun",
    responses=_error_responses(404),
)
async def cancel_run(run_id: UUID, body: RunCancelRequest, service: Service):
    return await service.cancel_run(run_id, reason=body.reason)


@router.post(
    "/runs/{run_id}/resume",
    response_model=RunResumeResponse,
    operation_id="resumeRun",
    responses=_error_responses(404, 409),
)
async def resume_run(run_id: UUID, body: RunResumeRequest, service: Service):
    return await service.resume_run(run_id, review_id=body.review_id)


@router.get(
    "/conversations/{conversation_id}/reviews",
    response_model=ReviewListResponse,
    operation_id="listReviews",
    responses=_error_responses(400, 404),
)
async def list_reviews(
    conversation_id: UUID,
    service: Service,
    run_id: UUID | None = None,
    review_status: ReviewStatus | None = Query(default=None, alias="status"),
    cursor: str | None = Query(default=None, max_length=2_048),
    limit: int = Query(default=50, ge=1, le=100),
):
    return await service.list_reviews(
        conversation_id,
        run_id=run_id,
        status=review_status,
        cursor=cursor,
        limit=limit,
    )


@router.post(
    "/reviews/{review_id}/decision",
    response_model=ReviewDecisionResponse,
    operation_id="decideReview",
    responses=_error_responses(404, 409),
)
async def decide_review(
    review_id: UUID,
    body: ReviewDecisionRequest,
    service: Service,
):
    return await service.decide_review(
        review_id,
        decision=body.decision,
        comment=body.comment,
    )


@router.get(
    "/conversations/{conversation_id}/artifacts",
    response_model=ArtifactListResponse,
    operation_id="listArtifacts",
    responses=_error_responses(400, 404),
)
async def list_artifacts(
    conversation_id: UUID,
    service: Service,
    run_id: UUID | None = None,
    kind: str | None = Query(default=None, max_length=128),
    cursor: str | None = Query(default=None, max_length=2_048),
    limit: int = Query(default=50, ge=1, le=100),
):
    return await service.list_artifacts(
        conversation_id,
        run_id=run_id,
        kind=kind,
        cursor=cursor,
        limit=limit,
    )


@router.get(
    "/artifacts/{artifact_id}",
    response_model=ArtifactRead,
    operation_id="getArtifact",
    responses=_error_responses(404),
)
async def get_artifact(artifact_id: UUID, service: Service):
    return await service.get_artifact(artifact_id)


@router.post(
    "/conversations/{conversation_id}/artifacts",
    response_model=ArtifactRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="uploadArtifact",
    responses=_error_responses(400, 404, 413),
)
async def upload_artifact(
    conversation_id: UUID,
    service: Service,
    file: Annotated[UploadFile, File(description="要导入 conversation workspace 的文件")],
    kind: Annotated[
        str,
        Form(pattern=r"^[a-z][a-z0-9_.-]{0,127}$"),
    ] = "dataset",
):
    try:
        return await service.upload_artifact(
            conversation_id,
            source=file.file,
            filename=file.filename,
            kind=kind,
            media_type=file.content_type,
        )
    finally:
        await file.close()


@router.get(
    "/artifacts/{artifact_id}/content",
    response_class=StreamingResponse,
    operation_id="getArtifactContent",
    responses={
        200: {
            "description": "返回经 workspace 边界校验的 artifact 内容。",
            "content": {"application/octet-stream": {}},
        },
        **_error_responses(400, 404),
    },
)
async def get_artifact_content(artifact_id: UUID, service: Service):
    content = await service.get_artifact_content(artifact_id)
    encoded_filename = quote(content.filename, safe="")
    return StreamingResponse(
        _artifact_chunks(content.stream),
        media_type=content.media_type or "application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=utf-8''{encoded_filename}",
            "Content-Length": str(content.size_bytes),
            "X-Content-Type-Options": "nosniff",
        },
        background=BackgroundTask(content.stream.close),
    )


__all__ = ["get_api_service", "get_readiness_service", "router"]
