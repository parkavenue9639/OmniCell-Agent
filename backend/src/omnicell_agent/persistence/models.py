"""Application-owned PostgreSQL models.

LangGraph checkpoint tables deliberately do not live in this metadata.  They
are owned by the checkpoint saver's migrations (AD-012).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from omnicell_agent.runs.status import ReviewStatus, RunStatus, TaskStatus


APP_SCHEMA = "omnicell_app"

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base for the application schema only."""

    metadata = MetaData(schema=APP_SCHEMA, naming_convention=NAMING_CONVENTION)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    title: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(32), default="active", server_default="active")
    workspace_uri: Mapped[str] = mapped_column(String(2048))
    dataset_uri: Mapped[str | None] = mapped_column(String(2048))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "request_key",
            name="uq_runs_conversation_request_key",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'review_required', 'cancelling', "
            "'completed', 'failed', 'cancelled')",
            name="run_status",
        ),
        CheckConstraint("attempt >= 0", name="run_attempt_non_negative"),
        Index("ix_runs_conversation_created", "conversation_id", "created_at"),
        Index("ix_runs_status_lease", "status", "lease_expires_at"),
        Index(
            "uq_runs_one_active_per_conversation",
            "conversation_id",
            unique=True,
            postgresql_where=text(
                "status IN ('pending', 'running', 'review_required', 'cancelling')"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{APP_SCHEMA}.conversations.id", ondelete="CASCADE")
    )
    request_key: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(
        String(32),
        default=RunStatus.PENDING.value,
        server_default=RunStatus.PENDING.value,
    )
    attempt: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    worker_id: Mapped[str | None] = mapped_column(String(255))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    request_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default="{}"
    )
    next_event_sequence: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0"
    )
    checkpoint_thread_id: Mapped[str | None] = mapped_column(String(255))
    error_summary: Mapped[str | None] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RunEvent(Base):
    __tablename__ = "run_events"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_run_events_run_sequence"),
        Index("ix_run_events_run_cursor", "run_id", "sequence"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{APP_SCHEMA}.runs.id", ondelete="CASCADE")
    )
    sequence: Mapped[int] = mapped_column(BigInteger)
    event_type: Mapped[str] = mapped_column(String(128))
    schema_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    run_status: Mapped[str | None] = mapped_column(String(32))
    error_summary: Mapped[str | None] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RunTask(Base):
    """A bounded, queryable projection of one Agent capability/tool task."""

    __tablename__ = "run_tasks"
    __table_args__ = (
        UniqueConstraint("run_id", "tool_call_id", name="uq_run_tasks_run_tool_call"),
        CheckConstraint(
            "status IN ('pending', 'in_progress', 'completed', 'failed', 'cancelled')",
            name="run_task_status",
        ),
        Index("ix_run_tasks_run_created", "run_id", "created_at"),
        Index("ix_run_tasks_run_status", "run_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{APP_SCHEMA}.conversations.id", ondelete="CASCADE")
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{APP_SCHEMA}.runs.id", ondelete="CASCADE")
    )
    tool_call_id: Mapped[str] = mapped_column(String(255))
    capability_name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(
        String(32),
        default=TaskStatus.PENDING.value,
        server_default=TaskStatus.PENDING.value,
    )
    request_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default="{}"
    )
    error_summary: Mapped[str | None] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Review(Base):
    """A persisted human-review gate bound to an exact checkpoint."""

    __tablename__ = "reviews"
    __table_args__ = (
        UniqueConstraint("run_id", "tool_call_id", name="uq_reviews_run_tool_call"),
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'cancelled')",
            name="review_status",
        ),
        Index("ix_reviews_conversation_created", "conversation_id", "created_at"),
        Index("ix_reviews_run_status", "run_id", "status"),
        Index(
            "ix_reviews_checkpoint",
            "checkpoint_thread_id",
            "checkpoint_ns",
            "checkpoint_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{APP_SCHEMA}.conversations.id", ondelete="CASCADE")
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{APP_SCHEMA}.runs.id", ondelete="CASCADE")
    )
    capability_name: Mapped[str] = mapped_column(String(128))
    tool_call_id: Mapped[str] = mapped_column(String(255))
    checkpoint_thread_id: Mapped[str] = mapped_column(String(255))
    checkpoint_ns: Mapped[str] = mapped_column(
        String(512), default="", server_default=""
    )
    checkpoint_id: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(
        String(32),
        default=ReviewStatus.PENDING.value,
        server_default=ReviewStatus.PENDING.value,
    )
    request_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default="{}"
    )
    decision_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        Index("ix_artifacts_conversation_created", "conversation_id", "created_at"),
        Index("ix_artifacts_run_created", "run_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{APP_SCHEMA}.conversations.id", ondelete="CASCADE")
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{APP_SCHEMA}.runs.id", ondelete="SET NULL")
    )
    source_event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{APP_SCHEMA}.run_events.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(String(128))
    uri: Mapped[str] = mapped_column(String(2048))
    media_type: Mapped[str | None] = mapped_column(String(255))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[str | None] = mapped_column(String(64))
    artifact_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class CheckpointAnchor(Base):
    """A lightweight retention reference, never the checkpoint payload itself."""

    __tablename__ = "checkpoint_anchors"
    __table_args__ = (
        UniqueConstraint(
            "thread_id",
            "checkpoint_ns",
            "checkpoint_id",
            "anchor_kind",
            name="uq_checkpoint_anchors_identity_kind",
        ),
        Index(
            "ix_checkpoint_anchors_lookup",
            "thread_id",
            "checkpoint_ns",
            "checkpoint_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{APP_SCHEMA}.conversations.id", ondelete="CASCADE")
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{APP_SCHEMA}.runs.id", ondelete="CASCADE")
    )
    thread_id: Mapped[str] = mapped_column(String(255))
    checkpoint_ns: Mapped[str] = mapped_column(String(512), default="", server_default="")
    checkpoint_id: Mapped[str] = mapped_column(String(255))
    anchor_kind: Mapped[str] = mapped_column(String(64))
    protected_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
