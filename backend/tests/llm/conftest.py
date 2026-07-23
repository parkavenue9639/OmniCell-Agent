from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import pytest

from omnicell_agent.llm import BaseLLMProvider, ModelCapabilities, ModelNotAllowedError


@dataclass(frozen=True)
class FakeModel:
    provider: str
    model: str
    options: Mapping[str, Any]


class FakeProvider(BaseLLMProvider):
    def __init__(
        self,
        name: str,
        *,
        default_model: str = "model-default",
        allowed: set[str] | None = None,
        capabilities: ModelCapabilities | None = None,
    ) -> None:
        self._name = name
        self._default_model = default_model
        self._allowed = allowed
        self._capabilities = capabilities or ModelCapabilities(
            structured_output=True,
            streaming=True,
            tool_calling=True,
        )
        self.created: list[FakeModel] = []
        self.validate_calls = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def default_model(self) -> str:
        return self._default_model

    def validate(self) -> None:
        self.validate_calls += 1
        if not self._name or not self._default_model:
            raise ValueError("invalid fake provider")

    def resolve_model(self, model: str) -> str:
        if not model.strip():
            raise ValueError("empty model")
        resolved = self._default_model if model == "default" else model
        if self._allowed is not None and resolved not in self._allowed:
            raise ModelNotAllowedError(f"not allowed: {resolved}")
        return resolved

    def capabilities_for(self, model: str) -> ModelCapabilities:
        self.resolve_model(model)
        return self._capabilities

    def create_model(self, model: str, **overrides: Any) -> FakeModel:
        created = FakeModel(self._name, self.resolve_model(model), dict(overrides))
        self.created.append(created)
        return created

    def validate_options(self, options: Mapping[str, Any]) -> None:
        return None

    def safe_info(self) -> Mapping[str, Any]:
        return {"name": self._name, "type": "fake", "default_model": self._default_model}


@pytest.fixture(autouse=True)
def _reset_process_default_factory():
    from omnicell_agent.llm import reset_default_factory

    reset_default_factory()
    yield
    reset_default_factory()
