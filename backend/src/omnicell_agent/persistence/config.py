from __future__ import annotations

import os
import re
from dataclasses import dataclass

from sqlalchemy.engine import URL, make_url


_SCHEMA_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


def _positive_int(value: int, *, name: str) -> int:
    if value < 1:
        raise ValueError(f"{name} 必须大于 0")
    return value


def _validate_schema(value: str, *, name: str) -> str:
    if not _SCHEMA_RE.fullmatch(value):
        raise ValueError(f"{name} 不是安全的 PostgreSQL schema 名称: {value!r}")
    return value


@dataclass(frozen=True, slots=True)
class PostgresSettings:
    """应用持久化和 LangGraph checkpointer 共用的 PostgreSQL 连接配置。"""

    dsn: str
    app_schema: str = "omnicell_app"
    checkpoint_schema: str = "omnicell_checkpoint"
    pool_min_size: int = 1
    pool_max_size: int = 8
    connect_timeout_seconds: float = 10.0
    event_payload_max_bytes: int = 256 * 1024
    artifact_metadata_max_bytes: int = 64 * 1024
    checkpoint_state_max_bytes: int = 2 * 1024 * 1024

    def __post_init__(self) -> None:
        if not self.dsn.strip():
            raise ValueError("PostgreSQL DSN 不能为空")
        _validate_schema(self.app_schema, name="app_schema")
        _validate_schema(self.checkpoint_schema, name="checkpoint_schema")
        if self.app_schema == self.checkpoint_schema:
            raise ValueError("app_schema 与 checkpoint_schema 必须相互隔离")
        _positive_int(self.pool_min_size, name="pool_min_size")
        _positive_int(self.pool_max_size, name="pool_max_size")
        if self.pool_min_size > self.pool_max_size:
            raise ValueError("pool_min_size 不能大于 pool_max_size")
        if self.connect_timeout_seconds <= 0:
            raise ValueError("connect_timeout_seconds 必须大于 0")
        for name in (
            "event_payload_max_bytes",
            "artifact_metadata_max_bytes",
            "checkpoint_state_max_bytes",
        ):
            _positive_int(getattr(self, name), name=name)

    @classmethod
    def from_env(cls) -> "PostgresSettings":
        dsn = os.environ.get("OMNICELL_POSTGRES_DSN", "").strip()
        if not dsn:
            raise RuntimeError("OMNICELL_POSTGRES_DSN 未设置")
        return cls(
            dsn=dsn,
            app_schema=os.environ.get("OMNICELL_POSTGRES_APP_SCHEMA", "omnicell_app"),
            checkpoint_schema=os.environ.get(
                "OMNICELL_POSTGRES_CHECKPOINT_SCHEMA", "omnicell_checkpoint"
            ),
            pool_min_size=int(os.environ.get("OMNICELL_POSTGRES_POOL_MIN", "1")),
            pool_max_size=int(os.environ.get("OMNICELL_POSTGRES_POOL_MAX", "8")),
            connect_timeout_seconds=float(
                os.environ.get("OMNICELL_POSTGRES_CONNECT_TIMEOUT", "10")
            ),
        )

    @property
    def sqlalchemy_dsn(self) -> str:
        url = make_url(self.dsn)
        if url.drivername in {"postgres", "postgresql"}:
            url = url.set(drivername="postgresql+psycopg")
        elif url.drivername != "postgresql+psycopg":
            raise ValueError(f"仅支持 PostgreSQL psycopg DSN，当前为 {url.drivername!r}")
        return url.render_as_string(hide_password=False)

    @property
    def psycopg_conninfo(self) -> str:
        url = make_url(self.dsn)
        if url.drivername == "postgresql+psycopg":
            url = url.set(drivername="postgresql")
        elif url.drivername == "postgres":
            url = url.set(drivername="postgresql")
        elif url.drivername != "postgresql":
            raise ValueError(f"仅支持 PostgreSQL DSN，当前为 {url.drivername!r}")
        return url.render_as_string(hide_password=False)

    @property
    def safe_target(self) -> str:
        # Query parameters may contain driver-specific credentials such as
        # sslpassword.  Operational logs only need the network/database target.
        source = make_url(self.sqlalchemy_dsn)
        url = URL.create(
            drivername=source.drivername,
            host=source.host,
            port=source.port,
            database=source.database,
        )
        return url.render_as_string(hide_password=True)
