"""增加 Agent run 生命周期、task 与 review 投影。

Revision ID: 20260722_0002
Revises: 20260722_0001
Create Date: 2026-07-22
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260722_0002"
down_revision: str | None = "20260722_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_RUN_STATUS_CHECK = (
    "status IN ('pending', 'running', 'review_required', 'cancelling', "
    "'completed', 'failed', 'cancelled')"
)
_ACTIVE_RUN_PREDICATE = (
    "status IN ('pending', 'running', 'review_required', 'cancelling')"
)
_TASK_STATUS_CHECK = (
    "status IN ('pending', 'in_progress', 'completed', 'failed', 'cancelled')"
)
_REVIEW_STATUS_CHECK = "status IN ('pending', 'approved', 'rejected', 'cancelled')"


def upgrade(app_schema: str | None = None) -> None:
    schema = app_schema or op.get_context().opts["app_schema"]

    op.add_column(
        "runs",
        sa.Column("attempt", sa.Integer(), server_default="0", nullable=False),
        schema=schema,
    )
    op.add_column(
        "runs", sa.Column("worker_id", sa.String(length=255), nullable=True), schema=schema
    )
    op.add_column(
        "runs",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        schema=schema,
    )
    op.add_column(
        "runs",
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        schema=schema,
    )
    op.add_column(
        "runs",
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        schema=schema,
    )
    op.create_check_constraint("run_status", "runs", _RUN_STATUS_CHECK, schema=schema)
    op.create_check_constraint(
        "run_attempt_non_negative", "runs", "attempt >= 0", schema=schema
    )
    op.create_index(
        "ix_runs_status_lease",
        "runs",
        ["status", "lease_expires_at"],
        schema=schema,
    )
    op.create_index(
        "uq_runs_one_active_per_conversation",
        "runs",
        ["conversation_id"],
        unique=True,
        schema=schema,
        postgresql_where=sa.text(_ACTIVE_RUN_PREDICATE),
    )

    op.add_column(
        "run_events",
        sa.Column("run_status", sa.String(length=32), nullable=True),
        schema=schema,
    )
    op.add_column(
        "run_events",
        sa.Column("error_summary", sa.String(length=2000), nullable=True),
        schema=schema,
    )

    op.create_table(
        "run_tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("tool_call_id", sa.String(length=255), nullable=False),
        sa.Column("capability_name", sa.String(length=128), nullable=False),
        sa.Column(
            "status", sa.String(length=32), server_default="pending", nullable=False
        ),
        sa.Column(
            "request_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("error_summary", sa.String(length=2000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(_TASK_STATUS_CHECK, name="run_task_status"),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            [f"{schema}.conversations.id"],
            name="fk_run_tasks_conversation_id_conversations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            [f"{schema}.runs.id"],
            name="fk_run_tasks_run_id_runs",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_run_tasks"),
        sa.UniqueConstraint(
            "run_id", "tool_call_id", name="uq_run_tasks_run_tool_call"
        ),
        schema=schema,
    )
    op.create_index(
        "ix_run_tasks_run_created",
        "run_tasks",
        ["run_id", "created_at"],
        schema=schema,
    )
    op.create_index(
        "ix_run_tasks_run_status",
        "run_tasks",
        ["run_id", "status"],
        schema=schema,
    )

    op.create_table(
        "reviews",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("capability_name", sa.String(length=128), nullable=False),
        sa.Column("tool_call_id", sa.String(length=255), nullable=False),
        sa.Column("checkpoint_thread_id", sa.String(length=255), nullable=False),
        sa.Column(
            "checkpoint_ns", sa.String(length=512), server_default="", nullable=False
        ),
        sa.Column("checkpoint_id", sa.String(length=255), nullable=False),
        sa.Column(
            "status", sa.String(length=32), server_default="pending", nullable=False
        ),
        sa.Column(
            "request_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "decision_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(_REVIEW_STATUS_CHECK, name="review_status"),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            [f"{schema}.conversations.id"],
            name="fk_reviews_conversation_id_conversations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            [f"{schema}.runs.id"],
            name="fk_reviews_run_id_runs",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_reviews"),
        sa.UniqueConstraint("run_id", "tool_call_id", name="uq_reviews_run_tool_call"),
        schema=schema,
    )
    op.create_index(
        "ix_reviews_conversation_created",
        "reviews",
        ["conversation_id", "created_at"],
        schema=schema,
    )
    op.create_index(
        "ix_reviews_run_status",
        "reviews",
        ["run_id", "status"],
        schema=schema,
    )
    op.create_index(
        "ix_reviews_checkpoint",
        "reviews",
        ["checkpoint_thread_id", "checkpoint_ns", "checkpoint_id"],
        schema=schema,
    )


def downgrade(app_schema: str | None = None) -> None:
    schema = app_schema or op.get_context().opts["app_schema"]

    op.drop_index("ix_reviews_checkpoint", table_name="reviews", schema=schema)
    op.drop_index("ix_reviews_run_status", table_name="reviews", schema=schema)
    op.drop_index(
        "ix_reviews_conversation_created", table_name="reviews", schema=schema
    )
    op.drop_table("reviews", schema=schema)

    op.drop_index("ix_run_tasks_run_status", table_name="run_tasks", schema=schema)
    op.drop_index("ix_run_tasks_run_created", table_name="run_tasks", schema=schema)
    op.drop_table("run_tasks", schema=schema)

    op.drop_column("run_events", "error_summary", schema=schema)
    op.drop_column("run_events", "run_status", schema=schema)

    op.drop_index(
        "uq_runs_one_active_per_conversation", table_name="runs", schema=schema
    )
    op.drop_index("ix_runs_status_lease", table_name="runs", schema=schema)
    op.drop_constraint(
        "run_attempt_non_negative", "runs", type_="check", schema=schema
    )
    op.drop_constraint("run_status", "runs", type_="check", schema=schema)
    op.drop_column("runs", "cancel_requested_at", schema=schema)
    op.drop_column("runs", "last_heartbeat_at", schema=schema)
    op.drop_column("runs", "lease_expires_at", schema=schema)
    op.drop_column("runs", "worker_id", schema=schema)
    op.drop_column("runs", "attempt", schema=schema)
