"""增加跨会话记忆的应用持久化资源。

Revision ID: 20260726_0003
Revises: 20260722_0002
Create Date: 2026-07-26
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260726_0003"
down_revision: str | None = "20260722_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_MEMORY_KIND_CHECK = (
    "kind IN ('response_preference', 'profile_fact', 'project_context', "
    "'scientific_observation')"
)
_MEMORY_ITEM_STATUS_CHECK = "status IN ('proposed', 'active', 'revoked', 'purged')"
_MEMORY_SOURCE_KIND_CHECK = "source_kind IN ('explicit', 'proposed', 'corrected')"
_MEMORY_SNAPSHOT_MODE_CHECK = "mode IN ('off', 'default', 'selected')"
_MEMORY_SNAPSHOT_OUTCOME_CHECK = "outcome IN ('loaded', 'empty', 'degraded')"
_MEMORY_SELECTION_REASON_CHECK = (
    "selection_reason IN ('default', 'selected', 'tool_search')"
)
_SHA256_CHECK = "~ '^[0-9a-f]{64}$'"


def upgrade(app_schema: str | None = None) -> None:
    schema = app_schema or op.get_context().opts["app_schema"]

    op.create_table(
        "memory_settings",
        sa.Column("scope_key", sa.String(length=64), nullable=False),
        sa.Column(
            "use_enabled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "generation_enabled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "tools_enabled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("provider_consent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_consent_version", sa.String(length=64), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "disclosure_epoch",
            sa.BigInteger(),
            server_default="1",
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
        sa.CheckConstraint("version > 0", name="memory_settings_version_positive"),
        sa.CheckConstraint(
            "disclosure_epoch > 0",
            name="memory_settings_disclosure_epoch_positive",
        ),
        sa.PrimaryKeyConstraint("scope_key", name="pk_memory_settings"),
        schema=schema,
    )
    op.execute(
        sa.text(
            f"""
            INSERT INTO "{schema}".memory_settings (
                scope_key,
                use_enabled,
                generation_enabled,
                tools_enabled,
                version,
                disclosure_epoch
            )
            VALUES ('local-default', false, false, false, 1, 1)
            ON CONFLICT (scope_key) DO NOTHING
            """
        )
    )

    op.create_table(
        "memory_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scope_key", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("stable_key", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=True),
        sa.Column(
            "dataset_scope",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("origin_run_id", sa.Uuid(), nullable=True),
        sa.Column("origin_attempt", sa.Integer(), nullable=True),
        sa.Column("origin_tool_call_id", sa.String(length=255), nullable=True),
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
        sa.CheckConstraint(_MEMORY_KIND_CHECK, name="memory_item_kind"),
        sa.CheckConstraint(_MEMORY_ITEM_STATUS_CHECK, name="memory_item_status"),
        sa.CheckConstraint(
            "current_version IS NULL OR current_version > 0",
            name="memory_item_current_version_positive",
        ),
        sa.CheckConstraint(
            "(origin_run_id IS NULL AND origin_attempt IS NULL AND "
            "origin_tool_call_id IS NULL) OR "
            "(origin_run_id IS NOT NULL AND origin_attempt IS NOT NULL AND "
            "origin_tool_call_id IS NOT NULL)",
            name="memory_item_origin_complete",
        ),
        sa.CheckConstraint(
            "origin_attempt IS NULL OR origin_attempt >= 0",
            name="memory_item_origin_attempt_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["scope_key"],
            [f"{schema}.memory_settings.scope_key"],
            name="fk_memory_items_scope_key_memory_settings",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_memory_items"),
        sa.UniqueConstraint(
            "scope_key",
            "stable_key",
            name="uq_memory_items_scope_stable_key",
        ),
        sa.UniqueConstraint(
            "origin_run_id",
            "origin_tool_call_id",
            name="uq_memory_items_origin_tool_call",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_memory_items_scope_status_updated",
        "memory_items",
        ["scope_key", "status", "updated_at"],
        schema=schema,
    )
    op.create_index(
        "ix_memory_items_scope_kind_updated",
        "memory_items",
        ["scope_key", "kind", "updated_at"],
        schema=schema,
    )

    op.create_table(
        "memory_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("source_kind", sa.String(length=16), nullable=False),
        sa.Column(
            "dataset_scope",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "source_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "version_number > 0",
            name="memory_version_number_positive",
        ),
        sa.CheckConstraint(
            "char_length(content) BETWEEN 1 AND 8000",
            name="memory_version_content_length",
        ),
        sa.CheckConstraint(
            f"sha256 {_SHA256_CHECK}",
            name="memory_version_sha256",
        ),
        sa.CheckConstraint(
            f"fingerprint {_SHA256_CHECK}",
            name="memory_version_fingerprint",
        ),
        sa.CheckConstraint(
            _MEMORY_SOURCE_KIND_CHECK,
            name="memory_version_source_kind",
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            [f"{schema}.memory_items.id"],
            name="fk_memory_versions_item_id_memory_items",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_memory_versions"),
        sa.UniqueConstraint(
            "item_id",
            "version_number",
            name="uq_memory_versions_item_version",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_memory_versions_item_created",
        "memory_versions",
        ["item_id", "created_at"],
        schema=schema,
    )
    op.create_index(
        "ix_memory_versions_fingerprint",
        "memory_versions",
        ["fingerprint"],
        schema=schema,
    )

    op.create_table(
        "memory_suppressions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scope_key", sa.String(length=64), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=True),
        sa.Column("reason", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"fingerprint {_SHA256_CHECK}",
            name="memory_suppression_fingerprint",
        ),
        sa.ForeignKeyConstraint(
            ["scope_key"],
            [f"{schema}.memory_settings.scope_key"],
            name="fk_memory_suppressions_scope_key_memory_settings",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            [f"{schema}.memory_items.id"],
            name="fk_memory_suppressions_item_id_memory_items",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_memory_suppressions"),
        sa.UniqueConstraint(
            "scope_key",
            "fingerprint",
            name="uq_memory_suppressions_scope_fingerprint",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_memory_suppressions_item",
        "memory_suppressions",
        ["item_id"],
        schema=schema,
    )

    op.create_table(
        "run_memory_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("scope_key", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("query_sha256", sa.String(length=64), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(length=255), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column(
            "content_bytes",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("degraded_code", sa.String(length=128), nullable=True),
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
        sa.CheckConstraint(
            _MEMORY_SNAPSHOT_MODE_CHECK,
            name="run_memory_snapshot_mode",
        ),
        sa.CheckConstraint(
            _MEMORY_SNAPSHOT_OUTCOME_CHECK,
            name="run_memory_snapshot_outcome",
        ),
        sa.CheckConstraint(
            f"query_sha256 {_SHA256_CHECK}",
            name="run_memory_snapshot_query_sha256",
        ),
        sa.CheckConstraint(
            "policy_version > 0",
            name="run_memory_snapshot_policy_version_positive",
        ),
        sa.CheckConstraint(
            "attempt >= 0",
            name="run_memory_snapshot_attempt_non_negative",
        ),
        sa.CheckConstraint(
            "content_bytes >= 0",
            name="run_memory_snapshot_content_bytes_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            [f"{schema}.runs.id"],
            name="fk_run_memory_snapshots_run_id_runs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["scope_key"],
            [f"{schema}.memory_settings.scope_key"],
            name="fk_run_memory_snapshots_scope_key_memory_settings",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_run_memory_snapshots"),
        sa.UniqueConstraint(
            "run_id",
            name="uq_run_memory_snapshots_run",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_run_memory_snapshots_scope_created",
        "run_memory_snapshots",
        ["scope_key", "created_at"],
        schema=schema,
    )

    op.create_table(
        "run_memory_inputs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        # Intentionally no FK: purge deletes plaintext version rows while this
        # identity-only decision record remains replayable.
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("source_kind", sa.String(length=16), nullable=False),
        sa.Column("selection_reason", sa.String(length=128), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "version_number > 0",
            name="run_memory_input_version_positive",
        ),
        sa.CheckConstraint(
            f"content_sha256 {_SHA256_CHECK}",
            name="run_memory_input_sha256",
        ),
        sa.CheckConstraint(_MEMORY_KIND_CHECK, name="run_memory_input_kind"),
        sa.CheckConstraint(
            _MEMORY_SOURCE_KIND_CHECK,
            name="run_memory_input_source_kind",
        ),
        sa.CheckConstraint(
            _MEMORY_SELECTION_REASON_CHECK,
            name="run_memory_input_selection_reason",
        ),
        sa.CheckConstraint(
            "ordinal >= 0",
            name="run_memory_input_ordinal_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            [f"{schema}.run_memory_snapshots.id"],
            name="fk_run_memory_inputs_snapshot_id_run_memory_snapshots",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            [f"{schema}.memory_items.id"],
            name="fk_run_memory_inputs_item_id_memory_items",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_run_memory_inputs"),
        sa.UniqueConstraint(
            "snapshot_id",
            "ordinal",
            name="uq_run_memory_inputs_snapshot_ordinal",
        ),
        sa.UniqueConstraint(
            "snapshot_id",
            "version_id",
            name="uq_run_memory_inputs_snapshot_version",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_run_memory_inputs_snapshot_ordinal",
        "run_memory_inputs",
        ["snapshot_id", "ordinal"],
        schema=schema,
    )
    op.create_index(
        "ix_run_memory_inputs_item",
        "run_memory_inputs",
        ["item_id"],
        schema=schema,
    )

    op.create_table(
        "run_memory_searches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("tool_call_id", sa.String(length=255), nullable=False),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column("worker_id", sa.String(length=255), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("result_count", sa.Integer(), nullable=False),
        sa.Column(
            "result_identities",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"request_sha256 {_SHA256_CHECK}",
            name="run_memory_search_request_sha256",
        ),
        sa.CheckConstraint(
            "attempt >= 0",
            name="run_memory_search_attempt_non_negative",
        ),
        sa.CheckConstraint(
            "result_count >= 0 AND result_count <= 32",
            name="run_memory_search_result_count",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            [f"{schema}.runs.id"],
            name="fk_run_memory_searches_run_id_runs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            [f"{schema}.run_memory_snapshots.id"],
            name=(
                "fk_run_memory_searches_snapshot_id_"
                "run_memory_snapshots"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_run_memory_searches"),
        sa.UniqueConstraint(
            "run_id",
            "tool_call_id",
            name="uq_run_memory_searches_run_tool_call",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_run_memory_searches_snapshot",
        "run_memory_searches",
        ["snapshot_id"],
        schema=schema,
    )

    op.create_table(
        "run_memory_forget_intents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("tool_call_id", sa.String(length=255), nullable=False),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column("worker_id", sa.String(length=255), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column(
            "memory_identity",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"request_sha256 {_SHA256_CHECK}",
            name="run_memory_forget_intent_request_sha256",
        ),
        sa.CheckConstraint(
            "attempt >= 0",
            name="run_memory_forget_intent_attempt_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            [f"{schema}.runs.id"],
            name="fk_run_memory_forget_intents_run_id_runs",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_run_memory_forget_intents"),
        sa.UniqueConstraint(
            "run_id",
            "tool_call_id",
            name="uq_run_memory_forget_intents_run_tool_call",
        ),
        schema=schema,
    )

    op.create_table(
        "run_memory_proposals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("tool_call_id", sa.String(length=255), nullable=False),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column("worker_id", sa.String(length=255), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column(
            "memory_identity",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"request_sha256 {_SHA256_CHECK}",
            name="run_memory_proposal_request_sha256",
        ),
        sa.CheckConstraint(
            "attempt >= 0",
            name="run_memory_proposal_attempt_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            [f"{schema}.runs.id"],
            name="fk_run_memory_proposals_run_id_runs",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_run_memory_proposals"),
        sa.UniqueConstraint(
            "run_id",
            "tool_call_id",
            name="uq_run_memory_proposals_run_tool_call",
        ),
        schema=schema,
    )


def downgrade(app_schema: str | None = None) -> None:
    schema = app_schema or op.get_context().opts["app_schema"]

    op.drop_table("run_memory_proposals", schema=schema)
    op.drop_table("run_memory_forget_intents", schema=schema)

    op.drop_index(
        "ix_run_memory_searches_snapshot",
        table_name="run_memory_searches",
        schema=schema,
    )
    op.drop_table("run_memory_searches", schema=schema)

    op.drop_index(
        "ix_run_memory_inputs_item",
        table_name="run_memory_inputs",
        schema=schema,
    )
    op.drop_index(
        "ix_run_memory_inputs_snapshot_ordinal",
        table_name="run_memory_inputs",
        schema=schema,
    )
    op.drop_table("run_memory_inputs", schema=schema)

    op.drop_index(
        "ix_run_memory_snapshots_scope_created",
        table_name="run_memory_snapshots",
        schema=schema,
    )
    op.drop_table("run_memory_snapshots", schema=schema)

    op.drop_index(
        "ix_memory_suppressions_item",
        table_name="memory_suppressions",
        schema=schema,
    )
    op.drop_table("memory_suppressions", schema=schema)

    op.drop_index(
        "ix_memory_versions_fingerprint",
        table_name="memory_versions",
        schema=schema,
    )
    op.drop_index(
        "ix_memory_versions_item_created",
        table_name="memory_versions",
        schema=schema,
    )
    op.drop_table("memory_versions", schema=schema)

    op.drop_index(
        "ix_memory_items_scope_kind_updated",
        table_name="memory_items",
        schema=schema,
    )
    op.drop_index(
        "ix_memory_items_scope_status_updated",
        table_name="memory_items",
        schema=schema,
    )
    op.drop_table("memory_items", schema=schema)
    op.drop_table("memory_settings", schema=schema)
