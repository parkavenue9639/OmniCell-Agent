"""Instance-owned capability registry and invocation context."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel

from .artifacts import ConversationArtifactStore
from .contracts import CapabilityRequest, CapabilitySpec


class CapabilityRegistryError(RuntimeError):
    pass


@dataclass(frozen=True)
class CapabilityContext:
    conversation_id: UUID
    artifacts: ConversationArtifactStore

    def __post_init__(self) -> None:
        if self.artifacts.conversation_id != self.conversation_id:
            raise ValueError("capability context 与 artifact store conversation 不一致")


class CapabilityHandler(Protocol):
    spec: CapabilitySpec
    request_model: type[CapabilityRequest]
    result_model: type[BaseModel]

    def invoke(
        self,
        request: CapabilityRequest,
        context: CapabilityContext,
    ) -> BaseModel: ...


class CapabilityRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, CapabilityHandler] = {}

    def register(self, handler: CapabilityHandler) -> None:
        name = handler.spec.name
        if name in self._handlers:
            raise CapabilityRegistryError(f"capability 已注册：{name}")
        self._handlers[name] = handler

    def get(self, name: str) -> CapabilityHandler:
        try:
            return self._handlers[name]
        except KeyError as exc:
            raise CapabilityRegistryError(f"未知 capability：{name}") from exc

    @property
    def specs(self) -> tuple[CapabilitySpec, ...]:
        return tuple(handler.spec for handler in self._handlers.values())

    def invoke(
        self,
        name: str,
        payload: CapabilityRequest | dict[str, Any],
        context: CapabilityContext,
    ) -> BaseModel:
        handler = self.get(name)
        request = handler.request_model.model_validate(payload)
        result = handler.invoke(request, context)
        return handler.result_model.model_validate(result)


__all__ = [
    "CapabilityContext",
    "CapabilityHandler",
    "CapabilityRegistry",
    "CapabilityRegistryError",
]
