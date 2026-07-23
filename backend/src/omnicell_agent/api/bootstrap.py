"""Production composition root for the API process."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from omnicell_agent.agent import AgentLoopFactory
from omnicell_agent.capabilities.bootstrap import build_domain_capability_layer
from omnicell_agent.core.environment import load_project_environment
from omnicell_agent.llm.bootstrap import build_factory_from_env
from omnicell_agent.persistence.bootstrap import PersistenceRuntime
from omnicell_agent.persistence.config import PostgresSettings
from omnicell_agent.runs.coordinator import RunCoordinator

from .app import create_app
from .health import build_readiness_service
from .service import ApiService


@asynccontextmanager
async def api_lifespan(app: FastAPI):
    load_project_environment()
    persistence = PersistenceRuntime(PostgresSettings.from_env())
    await persistence.open()
    coordinator: RunCoordinator | None = None
    try:
        capabilities = build_domain_capability_layer()
        agent_factory = AgentLoopFactory(
            capabilities,
            llm_factory=build_factory_from_env(),
        )
        workspace_root = Path(
            os.environ.get("OMNICELL_WORKSPACE_ROOT", "data/conversations")
        )
        coordinator = RunCoordinator(
            persistence.unit_of_work,
            checkpointer=persistence.checkpoints.get_saver(),
            agent_factory=agent_factory,
            workspace_root=workspace_root,
        )
        app.state.api_service = ApiService(
            persistence.unit_of_work,
            coordinator,
        )
        app.state.readiness_service = build_readiness_service(persistence)
        await coordinator.recover()
        yield
    finally:
        app.state.readiness_service = None
        app.state.api_service = None
        if coordinator is not None:
            await coordinator.close()
        await persistence.close()


app = create_app(lifespan=api_lifespan)


__all__ = ["api_lifespan", "app"]
