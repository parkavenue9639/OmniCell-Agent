"""Provider 类型注册与安全构造边界。"""

from __future__ import annotations

from collections.abc import Callable
from types import MappingProxyType
from typing import Any, Mapping

from .errors import LLMConfigurationError, UnknownProviderTypeError
from .providers import BaseLLMProvider, OpenAICompatibleProvider

ProviderBuilder = Callable[..., BaseLLMProvider]


class ProviderRegistry:
    """进程内、可实例化的 provider 类型注册表。"""

    def __init__(self) -> None:
        self._builders: dict[str, ProviderBuilder] = {}

    def register(self, provider_type: str, builder: ProviderBuilder) -> None:
        normalized = provider_type.strip().lower()
        if not normalized:
            raise LLMConfigurationError("provider type 不能为空")
        if normalized in self._builders:
            raise LLMConfigurationError(f"provider type {normalized!r} 已注册")
        if not callable(builder):
            raise TypeError("provider builder 必须可调用")
        self._builders[normalized] = builder

    def create(self, provider_type: str, **config: Any) -> BaseLLMProvider:
        normalized = provider_type.strip().lower()
        builder = self._builders.get(normalized)
        if builder is None:
            raise UnknownProviderTypeError(f"未知 provider type: {normalized or '<empty>'!r}")
        provider = builder(**config)
        if not isinstance(provider, BaseLLMProvider):
            raise TypeError(f"provider type {normalized!r} 的 builder 返回了无效对象")
        return provider

    @property
    def registered_types(self) -> tuple[str, ...]:
        return tuple(sorted(self._builders))

    def safe_info(self) -> Mapping[str, Any]:
        return MappingProxyType({"registered_types": self.registered_types})


def build_default_provider_registry() -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register("openai_compatible", OpenAICompatibleProvider)
    return registry
