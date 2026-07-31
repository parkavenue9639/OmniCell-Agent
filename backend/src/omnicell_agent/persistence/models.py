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
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from omnicell_agent.runs.status import ReviewStatus, RunStatus, TaskStatus


APP_SCHEMA = "omnicell_app"
LOCAL_DEFAULT_MEMORY_SCOPE = "local-default"

MEMORY_KINDS = (
    "response_preference",
    "profile_fact",
    "project_context",
    "scientific_observation",
)
MEMORY_ITEM_STATUSES = ("proposed", "active", "revoked", "purged")
MEMORY_SOURCE_KINDS = ("explicit", "proposed", "corrected")
MEMORY_SNAPSHOT_MODES = ("off", "default", "selected")
MEMORY_SNAPSHOT_OUTCOMES = ("loaded", "empty", "degraded")
MEMORY_SELECTION_REASONS = ("default", "selected", "tool_search")

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


class MemorySettings(Base):
    """Local-installation memory controls.

    The singleton row is seeded by the application migration.  Keeping it in
    the application schema makes consent and feature gates durable without
    leaking them into LangGraph checkpoints.
    """

    __tablename__ = "memory_settings"
    __table_args__ = (
        CheckConstraint("version > 0", name="memory_settings_version_positive"),
        CheckConstraint(
            "disclosure_epoch > 0",
            name="memory_settings_disclosure_epoch_positive",
        ),
    )

    scope_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    use_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    generation_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    tools_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    provider_consent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    provider_consent_version: Mapped[str | None] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    disclosure_epoch: Mapped[int] = mapped_column(
        BigInteger, default=1, server_default="1"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MemoryItem(Base):
    """Stable memory identity whose content lives in immutable versions."""

    __tablename__ = "memory_items"
    __table_args__ = (
        UniqueConstraint(
            "scope_key",
            "stable_key",
            name="uq_memory_items_scope_stable_key",
        ),
        CheckConstraint(
            "kind IN ('response_preference', 'profile_fact', 'project_context', "
            "'scientific_observation')",
            name="memory_item_kind",
        ),
        CheckConstraint(
            "status IN ('proposed', 'active', 'revoked', 'purged')",
            name="memory_item_status",
        ),
        CheckConstraint(
            "current_version IS NULL OR current_version > 0",
            name="memory_item_current_version_positive",
        ),
        CheckConstraint(
            "(origin_run_id IS NULL AND origin_attempt IS NULL AND "
            "origin_tool_call_id IS NULL) OR "
            "(origin_run_id IS NOT NULL AND origin_attempt IS NOT NULL AND "
            "origin_tool_call_id IS NOT NULL)",
            name="memory_item_origin_complete",
        ),
        CheckConstraint(
            "origin_attempt IS NULL OR origin_attempt >= 0",
            name="memory_item_origin_attempt_non_negative",
        ),
        UniqueConstraint(
            "origin_run_id",
            "origin_tool_call_id",
            name="uq_memory_items_origin_tool_call",
        ),
        Index(
            "ix_memory_items_scope_status_updated",
            "scope_key",
            "status",
            "updated_at",
        ),
        Index(
            "ix_memory_items_scope_kind_updated",
            "scope_key",
            "kind",
            "updated_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    scope_key: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(
            f"{APP_SCHEMA}.memory_settings.scope_key",
            ondelete="RESTRICT",
        ),
    )
    kind: Mapped[str] = mapped_column(String(32))
    stable_key: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16))
    current_version: Mapped[int | None] = mapped_column(Integer)
    dataset_scope: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    origin_run_id: Mapped[uuid.UUID | None] = mapped_column()
    origin_attempt: Mapped[int | None] = mapped_column(Integer)
    origin_tool_call_id: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MemoryVersion(Base):
    """Immutable plaintext memory version.

    Run snapshots reference its identity and digest, not its body.  Purge can
    therefore delete every version row while retaining non-plaintext audit and
    suppression records.
    """

    __tablename__ = "memory_versions"
    __table_args__ = (
        UniqueConstraint(
            "item_id",
            "version_number",
            name="uq_memory_versions_item_version",
        ),
        CheckConstraint(
            "version_number > 0",
            name="memory_version_number_positive",
        ),
        CheckConstraint(
            "char_length(content) BETWEEN 1 AND 8000",
            name="memory_version_content_length",
        ),
        CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$'",
            name="memory_version_sha256",
        ),
        CheckConstraint(
            "fingerprint ~ '^[0-9a-f]{64}$'",
            name="memory_version_fingerprint",
        ),
        CheckConstraint(
            "source_kind IN ('explicit', 'proposed', 'corrected')",
            name="memory_version_source_kind",
        ),
        Index("ix_memory_versions_item_created", "item_id", "created_at"),
        Index("ix_memory_versions_fingerprint", "fingerprint"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{APP_SCHEMA}.memory_items.id", ondelete="CASCADE")
    )
    version_number: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    fingerprint: Mapped[str] = mapped_column(String(64))
    source_kind: Mapped[str] = mapped_column(String(16))
    dataset_scope: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    source_refs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class MemorySuppression(Base):
    """Body-free tombstone for content or source-message suppression digests."""

    __tablename__ = "memory_suppressions"
    __table_args__ = (
        UniqueConstraint(
            "scope_key",
            "fingerprint",
            name="uq_memory_suppressions_scope_fingerprint",
        ),
        CheckConstraint(
            "fingerprint ~ '^[0-9a-f]{64}$'",
            name="memory_suppression_fingerprint",
        ),
        Index("ix_memory_suppressions_item", "item_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    scope_key: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(
            f"{APP_SCHEMA}.memory_settings.scope_key",
            ondelete="RESTRICT",
        ),
    )
    fingerprint: Mapped[str] = mapped_column(String(64))
    item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{APP_SCHEMA}.memory_items.id", ondelete="SET NULL")
    )
    reason: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RunMemorySnapshot(Base):
    """Attempt-fenced memory decision frozen for one run."""

    __tablename__ = "run_memory_snapshots"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_run_memory_snapshots_run"),
        CheckConstraint(
            "mode IN ('off', 'default', 'selected')",
            name="run_memory_snapshot_mode",
        ),
        CheckConstraint(
            "outcome IN ('loaded', 'empty', 'degraded')",
            name="run_memory_snapshot_outcome",
        ),
        CheckConstraint(
            "query_sha256 ~ '^[0-9a-f]{64}$'",
            name="run_memory_snapshot_query_sha256",
        ),
        CheckConstraint(
            "policy_version > 0",
            name="run_memory_snapshot_policy_version_positive",
        ),
        CheckConstraint(
            "attempt >= 0",
            name="run_memory_snapshot_attempt_non_negative",
        ),
        CheckConstraint(
            "content_bytes >= 0",
            name="run_memory_snapshot_content_bytes_non_negative",
        ),
        Index(
            "ix_run_memory_snapshots_scope_created",
            "scope_key",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{APP_SCHEMA}.runs.id", ondelete="CASCADE")
    )
    scope_key: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(
            f"{APP_SCHEMA}.memory_settings.scope_key",
            ondelete="RESTRICT",
        ),
    )
    mode: Mapped[str] = mapped_column(String(16))
    outcome: Mapped[str] = mapped_column(String(16))
    query_sha256: Mapped[str] = mapped_column(String(64))
    policy_version: Mapped[int] = mapped_column(Integer)
    worker_id: Mapped[str] = mapped_column(String(255))
    attempt: Mapped[int] = mapped_column(Integer)
    content_bytes: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0"
    )
    degraded_code: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RunMemoryInput(Base):
    """Identity-only memory input selected for a run.

    ``version_id`` intentionally has no foreign key so a true purge can remove
    plaintext versions without rewriting the historical run decision.
    """

    __tablename__ = "run_memory_inputs"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id",
            "ordinal",
            name="uq_run_memory_inputs_snapshot_ordinal",
        ),
        UniqueConstraint(
            "snapshot_id",
            "version_id",
            name="uq_run_memory_inputs_snapshot_version",
        ),
        CheckConstraint(
            "version_number > 0",
            name="run_memory_input_version_positive",
        ),
        CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name="run_memory_input_sha256",
        ),
        CheckConstraint(
            "kind IN ('response_preference', 'profile_fact', 'project_context', "
            "'scientific_observation')",
            name="run_memory_input_kind",
        ),
        CheckConstraint(
            "source_kind IN ('explicit', 'proposed', 'corrected')",
            name="run_memory_input_source_kind",
        ),
        CheckConstraint(
            "selection_reason IN ('default', 'selected', 'tool_search')",
            name="run_memory_input_selection_reason",
        ),
        CheckConstraint("ordinal >= 0", name="run_memory_input_ordinal_non_negative"),
        Index("ix_run_memory_inputs_snapshot_ordinal", "snapshot_id", "ordinal"),
        Index("ix_run_memory_inputs_item", "item_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{APP_SCHEMA}.run_memory_snapshots.id", ondelete="CASCADE")
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{APP_SCHEMA}.memory_items.id", ondelete="RESTRICT")
    )
    version_id: Mapped[uuid.UUID] = mapped_column()
    version_number: Mapped[int] = mapped_column(Integer)
    content_sha256: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(32))
    source_kind: Mapped[str] = mapped_column(String(16))
    selection_reason: Mapped[str] = mapped_column(String(128))
    ordinal: Mapped[int] = mapped_column(Integer)


class RunMemorySearch(Base):
    """Identity-only, idempotent result of one Agent memory search call."""

    __tablename__ = "run_memory_searches"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "tool_call_id",
            name="uq_run_memory_searches_run_tool_call",
        ),
        CheckConstraint(
            "request_sha256 ~ '^[0-9a-f]{64}$'",
            name="run_memory_search_request_sha256",
        ),
        CheckConstraint(
            "attempt >= 0",
            name="run_memory_search_attempt_non_negative",
        ),
        CheckConstraint(
            "result_count >= 0 AND result_count <= 32",
            name="run_memory_search_result_count",
        ),
        Index("ix_run_memory_searches_snapshot", "snapshot_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{APP_SCHEMA}.runs.id", ondelete="CASCADE")
    )
    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{APP_SCHEMA}.run_memory_snapshots.id", ondelete="CASCADE")
    )
    tool_call_id: Mapped[str] = mapped_column(String(255))
    request_sha256: Mapped[str] = mapped_column(String(64))
    worker_id: Mapped[str] = mapped_column(String(255))
    attempt: Mapped[int] = mapped_column(Integer)
    result_count: Mapped[int] = mapped_column(Integer)
    result_identities: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RunMemoryForgetIntent(Base):
    """Identity-only, idempotent result of one Agent forget request."""

    __tablename__ = "run_memory_forget_intents"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "tool_call_id",
            name="uq_run_memory_forget_intents_run_tool_call",
        ),
        CheckConstraint(
            "request_sha256 ~ '^[0-9a-f]{64}$'",
            name="run_memory_forget_intent_request_sha256",
        ),
        CheckConstraint(
            "attempt >= 0",
            name="run_memory_forget_intent_attempt_non_negative",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{APP_SCHEMA}.runs.id", ondelete="CASCADE")
    )
    tool_call_id: Mapped[str] = mapped_column(String(255))
    request_sha256: Mapped[str] = mapped_column(String(64))
    worker_id: Mapped[str] = mapped_column(String(255))
    attempt: Mapped[int] = mapped_column(Integer)
    memory_identity: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RunMemoryProposal(Base):
    """Immutable, identity-only result of one Agent proposal call."""

    __tablename__ = "run_memory_proposals"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "tool_call_id",
            name="uq_run_memory_proposals_run_tool_call",
        ),
        CheckConstraint(
            "request_sha256 ~ '^[0-9a-f]{64}$'",
            name="run_memory_proposal_request_sha256",
        ),
        CheckConstraint(
            "attempt >= 0",
            name="run_memory_proposal_attempt_non_negative",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{APP_SCHEMA}.runs.id", ondelete="CASCADE")
    )
    tool_call_id: Mapped[str] = mapped_column(String(255))
    request_sha256: Mapped[str] = mapped_column(String(64))
    worker_id: Mapped[str] = mapped_column(String(255))
    attempt: Mapped[int] = mapped_column(Integer)
    memory_identity: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


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
