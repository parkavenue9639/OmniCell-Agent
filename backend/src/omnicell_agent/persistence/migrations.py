"""Explicit Alembic entry points for the application schema."""

from __future__ import annotations

import asyncio
from importlib.resources import as_file, files
from pathlib import Path
from typing import Protocol

from alembic import command
from alembic.config import Config


class MigrationSettingsLike(Protocol):
    sqlalchemy_dsn: str
    app_schema: str


def _alembic_config(
    settings: MigrationSettingsLike,
    *,
    script_location: Path | None = None,
) -> Config:
    if script_location is None:
        script_location = Path(
            str(files("omnicell_agent.persistence").joinpath("alembic"))
        )
    config = Config()
    config.set_main_option("script_location", str(script_location))
    config.set_main_option("path_separator", "os")
    # Alembic's Config performs %-interpolation; escaped percent characters
    # preserve URL-encoded credentials without logging the resulting DSN.
    config.set_main_option("sqlalchemy.url", settings.sqlalchemy_dsn.replace("%", "%%"))
    config.attributes["app_schema"] = settings.app_schema
    return config


def upgrade_app_schema_sync(
    settings: MigrationSettingsLike, revision: str = "head"
) -> None:
    resource = files("omnicell_agent.persistence").joinpath("alembic")
    with as_file(resource) as script_location:
        command.upgrade(
            _alembic_config(settings, script_location=script_location),
            revision,
        )


async def upgrade_app_schema(
    settings: MigrationSettingsLike, revision: str = "head"
) -> None:
    await asyncio.to_thread(upgrade_app_schema_sync, settings, revision)
