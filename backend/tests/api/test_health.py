from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omnicell_agent.api import bootstrap
from omnicell_agent.api.app import create_app
from omnicell_agent.api.contracts import LivenessResponse, ReadinessResponse
from omnicell_agent.api.health import ReadinessService, build_readiness_service
from omnicell_agent.runtime import DockerCommandResult


async def _healthy() -> bool:
    return True


async def _unavailable() -> bool:
    return False


def test_liveness_only_represents_the_api_process() -> None:
    class ExplodingReadiness:
        async def check(self):
            raise AssertionError("liveness 不应执行依赖探针")

    client = TestClient(create_app(readiness_service=ExplodingReadiness()))  # type: ignore[arg-type]

    response = client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert LivenessResponse.model_validate(response.json()).status == "alive"


def test_readiness_reports_every_required_component() -> None:
    readiness = ReadinessService(
        postgres_application=_healthy,
        postgres_checkpointer=_healthy,
        execution_backend=_healthy,
    )
    client = TestClient(create_app(readiness_service=readiness))

    response = client.get("/api/v1/health/ready")

    assert response.status_code == 200
    payload = ReadinessResponse.model_validate(response.json())
    assert payload.ready is True
    assert payload.components.model_dump(mode="json") == {
        "api": "healthy",
        "postgres_application": "healthy",
        "postgres_checkpointer": "healthy",
        "execution_backend": "healthy",
    }


def test_readiness_fails_closed_before_the_composition_root_is_ready() -> None:
    response = TestClient(create_app()).get("/api/v1/health/ready")

    assert response.status_code == 503
    payload = ReadinessResponse.model_validate(response.json())
    assert payload.ready is False
    assert payload.components.api == "healthy"
    assert payload.components.postgres_application == "unavailable"
    assert payload.components.postgres_checkpointer == "unavailable"
    assert payload.components.execution_backend == "unavailable"


def test_readiness_bounds_each_probe_and_never_leaks_failure_details() -> None:
    async def credential_failure() -> bool:
        raise RuntimeError("postgresql://admin:secret@db/internal")

    async def hanging_docker() -> bool:
        await asyncio.Event().wait()
        return True

    readiness = ReadinessService(
        postgres_application=credential_failure,
        postgres_checkpointer=_healthy,
        execution_backend=hanging_docker,
        timeout_seconds=0.01,
    )
    client = TestClient(create_app(readiness_service=readiness))

    response = client.get("/api/v1/health/ready")

    assert response.status_code == 503
    payload = ReadinessResponse.model_validate(response.json())
    assert payload.ready is False
    assert payload.components.postgres_application == "unavailable"
    assert payload.components.postgres_checkpointer == "healthy"
    assert payload.components.execution_backend == "unavailable"
    serialized = response.text.casefold()
    assert "secret" not in serialized
    assert "postgresql" not in serialized
    assert "internal" not in serialized


def test_unavailable_component_does_not_hide_other_component_states() -> None:
    readiness = ReadinessService(
        postgres_application=_healthy,
        postgres_checkpointer=_unavailable,
        execution_backend=_healthy,
    )

    response = TestClient(create_app(readiness_service=readiness)).get(
        "/api/v1/health/ready"
    )

    assert response.status_code == 503
    payload = ReadinessResponse.model_validate(response.json())
    assert payload.components.postgres_application == "healthy"
    assert payload.components.postgres_checkpointer == "unavailable"
    assert payload.components.execution_backend == "healthy"


def test_production_readiness_reuses_persistence_and_bounds_docker_cli() -> None:
    class Application:
        is_open = True

        def __init__(self) -> None:
            self.calls = 0

        async def check_connection(self) -> None:
            self.calls += 1

    class Checkpoints:
        def __init__(self) -> None:
            self.calls = 0

        async def healthcheck(self) -> bool:
            self.calls += 1
            return True

    class Docker:
        def __init__(self) -> None:
            self.calls: list[tuple[tuple[str, ...], dict[str, Any]]] = []

        async def run(self, args, **kwargs) -> DockerCommandResult:
            argv = tuple(args)
            self.calls.append((argv, kwargs))
            return DockerCommandResult(argv, 0, b'"27.5.1"', b"")

    application = Application()
    checkpoints = Checkpoints()
    docker = Docker()
    persistence = SimpleNamespace(
        application=application,
        checkpoints=checkpoints,
    )
    readiness = build_readiness_service(  # type: ignore[arg-type]
        persistence,
        docker=docker,  # type: ignore[arg-type]
        timeout_seconds=0.25,
    )

    result = asyncio.run(readiness.check())

    assert result.ready is True
    assert application.calls == 1
    assert checkpoints.calls == 1
    assert docker.calls == [
        (
            ("info", "--format", "{{json .ServerVersion}}"),
            {
                "timeout": 0.25,
                "stdout_max_bytes": 4096,
                "stderr_max_bytes": 4096,
            },
        )
    ]


def test_production_lifespan_wires_and_clears_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifecycle: list[str] = []
    built: dict[str, object] = {}

    class Persistence:
        def __init__(self, settings: object) -> None:
            built["settings"] = settings
            self.unit_of_work = object()
            self.checkpoints = SimpleNamespace(get_saver=lambda: object())

        async def open(self) -> None:
            lifecycle.append("persistence.open")

        async def close(self) -> None:
            lifecycle.append("persistence.close")

    class Coordinator:
        def __init__(self, *args: object, **kwargs: object) -> None:
            built["coordinator"] = self

        async def recover(self) -> None:
            lifecycle.append("coordinator.recover")

        async def close(self) -> None:
            lifecycle.append("coordinator.close")

    readiness = object()
    api_service = object()

    monkeypatch.setattr(bootstrap, "PersistenceRuntime", Persistence)
    monkeypatch.setattr(
        bootstrap,
        "PostgresSettings",
        SimpleNamespace(from_env=lambda: "settings"),
    )
    monkeypatch.setattr(bootstrap, "build_domain_capability_layer", lambda: "caps")
    monkeypatch.setattr(bootstrap, "build_factory_from_env", lambda: "llm")
    monkeypatch.setattr(
        bootstrap,
        "AgentLoopFactory",
        lambda capabilities, *, llm_factory: (capabilities, llm_factory),
    )
    monkeypatch.setattr(bootstrap, "RunCoordinator", Coordinator)
    monkeypatch.setattr(
        bootstrap,
        "ApiService",
        lambda unit_of_work, coordinator: api_service,
    )

    def build_readiness(persistence: object) -> object:
        built["readiness_persistence"] = persistence
        return readiness

    monkeypatch.setattr(bootstrap, "build_readiness_service", build_readiness)

    async def exercise() -> None:
        app = FastAPI()
        async with bootstrap.api_lifespan(app):
            assert app.state.api_service is api_service
            assert app.state.readiness_service is readiness
            assert built["readiness_persistence"].__class__ is Persistence
            assert lifecycle == ["persistence.open", "coordinator.recover"]
        assert app.state.api_service is None
        assert app.state.readiness_service is None

    asyncio.run(exercise())
    assert lifecycle == [
        "persistence.open",
        "coordinator.recover",
        "coordinator.close",
        "persistence.close",
    ]
