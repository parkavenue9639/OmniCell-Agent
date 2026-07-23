"""API 存活与依赖就绪探针。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from omnicell_agent.persistence.bootstrap import PersistenceRuntime
from omnicell_agent.runtime import DockerCLI

from .contracts import (
    HealthComponentsRead,
    HealthComponentStatus,
    ReadinessResponse,
)

HealthProbe = Callable[[], Awaitable[bool]]


class ReadinessService:
    """并发执行有界探针，只向公共契约投影稳定的健康状态。"""

    def __init__(
        self,
        *,
        postgres_application: HealthProbe,
        postgres_checkpointer: HealthProbe,
        execution_backend: HealthProbe,
        timeout_seconds: float = 2.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("readiness timeout 必须为正数")
        self._postgres_application = postgres_application
        self._postgres_checkpointer = postgres_checkpointer
        self._execution_backend = execution_backend
        self._timeout_seconds = timeout_seconds

    async def check(self) -> ReadinessResponse:
        application_ok, checkpointer_ok, execution_ok = await asyncio.gather(
            self._run_probe(self._postgres_application),
            self._run_probe(self._postgres_checkpointer),
            self._run_probe(self._execution_backend),
        )
        components = HealthComponentsRead(
            api=HealthComponentStatus.HEALTHY,
            postgres_application=_component(application_ok),
            postgres_checkpointer=_component(checkpointer_ok),
            execution_backend=_component(execution_ok),
        )
        return ReadinessResponse(
            ready=application_ok and checkpointer_ok and execution_ok,
            components=components,
        )

    async def _run_probe(self, probe: HealthProbe) -> bool:
        try:
            result = await asyncio.wait_for(
                probe(),
                timeout=self._timeout_seconds,
            )
        except Exception:
            # 探针异常文本可能包含 DSN、Docker stderr 或宿主信息，不能进入公共响应。
            return False
        return result is True


def build_readiness_service(
    persistence: PersistenceRuntime,
    *,
    docker: DockerCLI | None = None,
    timeout_seconds: float = 2.0,
) -> ReadinessService:
    """从生产组合根资源构造 readiness；不创建第二套数据库连接。"""

    docker_cli = docker or DockerCLI()

    async def postgres_application() -> bool:
        if not persistence.application.is_open:
            return False
        await persistence.application.check_connection()
        return True

    async def postgres_checkpointer() -> bool:
        return await persistence.checkpoints.healthcheck()

    async def execution_backend() -> bool:
        await docker_cli.run(
            ("info", "--format", "{{json .ServerVersion}}"),
            timeout=timeout_seconds,
            stdout_max_bytes=4 * 1024,
            stderr_max_bytes=4 * 1024,
        )
        return True

    return ReadinessService(
        postgres_application=postgres_application,
        postgres_checkpointer=postgres_checkpointer,
        execution_backend=execution_backend,
        timeout_seconds=timeout_seconds,
    )


def unavailable_readiness() -> ReadinessResponse:
    """组合根尚未就绪时的 fail-closed 公共投影。"""

    return ReadinessResponse(
        ready=False,
        components=HealthComponentsRead(
            api=HealthComponentStatus.HEALTHY,
            postgres_application=HealthComponentStatus.UNAVAILABLE,
            postgres_checkpointer=HealthComponentStatus.UNAVAILABLE,
            execution_backend=HealthComponentStatus.UNAVAILABLE,
        ),
    )


def _component(healthy: bool) -> HealthComponentStatus:
    return (
        HealthComponentStatus.HEALTHY
        if healthy
        else HealthComponentStatus.UNAVAILABLE
    )


__all__ = [
    "HealthProbe",
    "ReadinessService",
    "build_readiness_service",
    "unavailable_readiness",
]
