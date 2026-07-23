"""Stable lifecycle values shared by persistence and orchestration."""

from __future__ import annotations

from enum import StrEnum


class InvalidRunTransitionError(ValueError):
    pass


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    REVIEW_REQUIRED = "review_required"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ReviewStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class ReviewDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


_TERMINAL_RUN_STATUSES = frozenset(
    {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}
)

_RUN_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.PENDING: frozenset(
        {RunStatus.RUNNING, RunStatus.CANCELLING, RunStatus.CANCELLED, RunStatus.FAILED}
    ),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.REVIEW_REQUIRED,
            RunStatus.CANCELLING,
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }
    ),
    RunStatus.REVIEW_REQUIRED: frozenset(
        {
            RunStatus.RUNNING,
            RunStatus.CANCELLING,
            RunStatus.CANCELLED,
            RunStatus.FAILED,
        }
    ),
    RunStatus.CANCELLING: frozenset({RunStatus.CANCELLED, RunStatus.FAILED}),
    RunStatus.COMPLETED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
}


def is_terminal_run_status(status: RunStatus | str) -> bool:
    return RunStatus(status) in _TERMINAL_RUN_STATUSES


def validate_run_transition(
    current: RunStatus | str,
    target: RunStatus | str,
    *,
    allow_idempotent: bool = True,
) -> None:
    source = RunStatus(current)
    destination = RunStatus(target)
    if source == destination and allow_idempotent:
        return
    if destination not in _RUN_TRANSITIONS[source]:
        raise InvalidRunTransitionError(
            f"非法 run 状态转换：{source.value} -> {destination.value}"
        )


__all__ = [
    "InvalidRunTransitionError",
    "ReviewDecision",
    "ReviewStatus",
    "RunStatus",
    "TaskStatus",
    "is_terminal_run_status",
    "validate_run_transition",
]
