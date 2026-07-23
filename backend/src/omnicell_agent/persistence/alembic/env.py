from __future__ import annotations

import asyncio
import re
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from omnicell_agent.persistence.models import APP_SCHEMA, Base


config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
_SCHEMA_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _app_schema() -> str:
    schema = str(config.attributes.get("app_schema", APP_SCHEMA))
    if not _SCHEMA_PATTERN.fullmatch(schema):
        raise ValueError(f"Invalid PostgreSQL schema identifier: {schema!r}")
    return schema


def _configure(connection: Connection | None = None) -> None:
    schema = _app_schema()
    options = {
        "target_metadata": target_metadata,
        "include_schemas": True,
        "version_table_schema": schema,
        "compare_type": True,
        "render_as_batch": False,
    }
    if connection is None:
        context.configure(
            url=config.get_main_option("sqlalchemy.url"),
            literal_binds=True,
            dialect_opts={"paramstyle": "named"},
            **options,
        )
    else:
        context.configure(
            connection=connection.execution_options(
                schema_translate_map={APP_SCHEMA: schema}
            ),
            **options,
        )


def run_migrations_offline() -> None:
    _configure()
    with context.begin_transaction():
        context.run_migrations(app_schema=_app_schema())


def _run_migrations(connection: Connection) -> None:
    schema = _app_schema()
    # The version table lives in the application schema, so the schema must
    # exist before Alembic inspects its revision.  Application tables remain
    # exclusively revision-owned.
    connection.exec_driver_sql(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations(app_schema=schema)


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    try:
        # `CREATE SCHEMA` below starts the external SQLAlchemy transaction before
        # Alembic enters its migration context.  Owning that transaction here is
        # essential: a bare `connect()` would roll all DDL and the revision row
        # back when the connection closes.
        async with connectable.begin() as connection:
            await connection.run_sync(_run_migrations)
    finally:
        await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
