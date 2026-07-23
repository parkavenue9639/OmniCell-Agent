"""初始化应用持久化 schema。

Revision ID: 20260722_0001
Revises:
Create Date: 2026-07-22
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260722_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade(app_schema: str | None = None) -> None:
    schema = app_schema or op.get_context().opts["app_schema"]
    op.execute(sa.text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))

    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
        sa.Column("workspace_uri", sa.String(length=2048), nullable=False),
        sa.Column("dataset_uri", sa.String(length=2048), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_conversations"),
        schema=schema,
    )
    op.create_table(
        "runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("request_key", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("request_payload", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("next_event_sequence", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("checkpoint_thread_id", sa.String(length=255), nullable=True),
        sa.Column("error_summary", sa.String(length=2000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["conversation_id"], [f"{schema}.conversations.id"], name="fk_runs_conversation_id_conversations", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_runs"),
        sa.UniqueConstraint("conversation_id", "request_key", name="uq_runs_conversation_request_key"),
        schema=schema,
    )
    op.create_index("ix_runs_conversation_created", "runs", ["conversation_id", "created_at"], schema=schema)

    op.create_table(
        "run_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("schema_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], [f"{schema}.runs.id"], name="fk_run_events_run_id_runs", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_run_events"),
        sa.UniqueConstraint("run_id", "sequence", name="uq_run_events_run_sequence"),
        schema=schema,
    )
    op.create_index("ix_run_events_run_cursor", "run_events", ["run_id", "sequence"], schema=schema)

    op.create_table(
        "artifacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("source_event_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(length=128), nullable=False),
        sa.Column("uri", sa.String(length=2048), nullable=False),
        sa.Column("media_type", sa.String(length=255), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], [f"{schema}.conversations.id"], name="fk_artifacts_conversation_id_conversations", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], [f"{schema}.runs.id"], name="fk_artifacts_run_id_runs", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_event_id"], [f"{schema}.run_events.id"], name="fk_artifacts_source_event_id_run_events", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_artifacts"),
        schema=schema,
    )
    op.create_index("ix_artifacts_conversation_created", "artifacts", ["conversation_id", "created_at"], schema=schema)
    op.create_index("ix_artifacts_run_created", "artifacts", ["run_id", "created_at"], schema=schema)

    op.create_table(
        "checkpoint_anchors",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("thread_id", sa.String(length=255), nullable=False),
        sa.Column("checkpoint_ns", sa.String(length=512), server_default="", nullable=False),
        sa.Column("checkpoint_id", sa.String(length=255), nullable=False),
        sa.Column("anchor_kind", sa.String(length=64), nullable=False),
        sa.Column("protected_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], [f"{schema}.conversations.id"], name="fk_checkpoint_anchors_conversation_id_conversations", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], [f"{schema}.runs.id"], name="fk_checkpoint_anchors_run_id_runs", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_checkpoint_anchors"),
        sa.UniqueConstraint("thread_id", "checkpoint_ns", "checkpoint_id", "anchor_kind", name="uq_checkpoint_anchors_identity_kind"),
        schema=schema,
    )
    op.create_index("ix_checkpoint_anchors_lookup", "checkpoint_anchors", ["thread_id", "checkpoint_ns", "checkpoint_id"], schema=schema)


def downgrade(app_schema: str | None = None) -> None:
    schema = app_schema or op.get_context().opts["app_schema"]
    op.drop_index("ix_checkpoint_anchors_lookup", table_name="checkpoint_anchors", schema=schema)
    op.drop_table("checkpoint_anchors", schema=schema)
    op.drop_index("ix_artifacts_run_created", table_name="artifacts", schema=schema)
    op.drop_index("ix_artifacts_conversation_created", table_name="artifacts", schema=schema)
    op.drop_table("artifacts", schema=schema)
    op.drop_index("ix_run_events_run_cursor", table_name="run_events", schema=schema)
    op.drop_table("run_events", schema=schema)
    op.drop_index("ix_runs_conversation_created", table_name="runs", schema=schema)
    op.drop_table("runs", schema=schema)
    op.drop_table("conversations", schema=schema)
