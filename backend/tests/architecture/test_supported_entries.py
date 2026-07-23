from __future__ import annotations

import ast
from pathlib import Path
import tomllib

from omnicell_agent.llm import LLMFactory
from omnicell_agent.runtime import LocalDockerPythonSession


BACKEND_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = BACKEND_ROOT / "src" / "omnicell_agent"


def test_removed_execution_entries_do_not_return() -> None:
    removed_paths = (
        PACKAGE_ROOT / "main.py",
        PACKAGE_ROOT / "sandbox" / "__init__.py",
        PACKAGE_ROOT / "sandbox" / "docker_manager.py",
        PACKAGE_ROOT / "core" / "llm_client.py",
    )

    assert all(not path.exists() for path in removed_paths)
    assert not hasattr(LLMFactory, "create_model")
    assert LocalDockerPythonSession.__module__ == "omnicell_agent.runtime.python_session"


def test_backend_exposes_only_lifecycle_and_database_commands() -> None:
    configuration = tomllib.loads(
        (BACKEND_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )

    assert configuration["project"]["scripts"] == {
        "omnicell-api": "omnicell_agent.api.cli:main",
        "omnicell-db": "omnicell_agent.persistence.cli:main",
    }


def test_api_composition_root_uses_agent_loop_and_run_coordinator() -> None:
    tree = ast.parse(
        (PACKAGE_ROOT / "api" / "bootstrap.py").read_text(encoding="utf-8")
    )
    imports = {
        (node.module, alias.name)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }

    assert ("omnicell_agent.agent", "AgentLoopFactory") in imports
    assert (
        "omnicell_agent.capabilities.bootstrap",
        "build_domain_capability_layer",
    ) in imports
    assert ("omnicell_agent.runs.coordinator", "RunCoordinator") in imports
    assert all(
        module not in {
            "omnicell_agent.pipeline.graph",
            "omnicell_agent.annotation.graph",
        }
        for module, _ in imports
    )


def test_generic_agent_loop_has_no_domain_capability_imports() -> None:
    source = (PACKAGE_ROOT / "agent" / "loop.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert all(
        not module.startswith("omnicell_agent.capabilities")
        for module in imported_modules
    )
    assert all(
        token not in source
        for token in (
            "Graph A",
            "Graph B",
            "ArtifactRef",
            "marker_table",
            "single_cell_analysis",
            "deep_cell_annotation",
        )
    )
