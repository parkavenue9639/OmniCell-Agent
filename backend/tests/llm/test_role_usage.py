from __future__ import annotations

import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _role_calls(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roles: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "get_llm_by_alias" or not node.args:
            continue
        role = node.args[0]
        if (
            isinstance(role, ast.Attribute)
            and isinstance(role.value, ast.Attribute)
            and isinstance(role.value.value, ast.Name)
            and role.value.value.id == "llm"
            and role.value.attr == "LLMRole"
        ):
            roles.append(role.attr)
    return roles


def test_domain_nodes_use_declared_llm_roles() -> None:
    expected = {
        "src/omnicell_agent/pipeline/nodes/planner.py": "FAST_ROUTER",
        "src/omnicell_agent/pipeline/nodes/context_resolver.py": "FAST_ROUTER",
        "src/omnicell_agent/pipeline/nodes/programmer.py": "CODE_GENERATION",
        "src/omnicell_agent/pipeline/nodes/evaluator.py": "VISION",
        "src/omnicell_agent/pipeline/nodes/summarizer.py": "SUMMARY",
        "src/omnicell_agent/annotation/nodes/annotator.py": "ANNOTATION",
        "src/omnicell_agent/annotation/nodes/boost.py": "ANNOTATION",
        "src/omnicell_agent/annotation/nodes/validator.py": "VALIDATION",
    }
    for relative_path, role in expected.items():
        path = BACKEND_ROOT / relative_path
        source = path.read_text(encoding="utf-8")
        assert _role_calls(path) == [role], relative_path


def test_core_config_does_not_own_llm_provider_configuration() -> None:
    source = (BACKEND_ROOT / "src/omnicell_agent/core/config.py").read_text(
        encoding="utf-8"
    )
    for setting in (
        "OPENROUTER_API_KEY",
        "OPENROUTER_BASE_URL",
        "DEFAULT_OPENROUTER_MODEL",
        "ONEROUTER_API_KEY",
        "ONEROUTER_BASE_URL",
        "DEFAULT_ONEROUTER_MODEL",
    ):
        assert setting not in source
