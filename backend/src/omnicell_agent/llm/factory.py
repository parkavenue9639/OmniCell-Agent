"""逻辑 alias 驱动的 LLM Factory。"""

from __future__ import annotations

import logging
from types import MappingProxyType
from typing import Any, Mapping

from .errors import (
    CapabilityMismatchError,
    LLMConfigurationError,
    UnknownAliasError,
    UnknownProviderError,
)
from .providers import BaseLLMProvider
from .types import AliasSpec, LLMRole, ModelCapabilities, ResolvedModel

logger = logging.getLogger(__name__)


def _alias_name(alias: str | LLMRole) -> str:
    value = alias.value if isinstance(alias, LLMRole) else str(alias)
    normalized = value.strip()
    if not normalized:
        raise UnknownAliasError("LLM alias 不能为空")
    return normalized


class LLMFactory:
    """Provider 实例、逻辑 alias 与模型创建的唯一组合边界。"""

    def __init__(
        self,
        providers: Mapping[str, BaseLLMProvider],
        aliases: Mapping[str | LLMRole, AliasSpec],
    ) -> None:
        normalized_providers: dict[str, BaseLLMProvider] = {}
        for key, provider in providers.items():
            name = key.strip()
            if not name:
                raise LLMConfigurationError("provider 映射键不能为空")
            if name in normalized_providers:
                raise LLMConfigurationError(f"provider {name!r} 重复")
            normalized_providers[name] = provider

        normalized_aliases: dict[str, AliasSpec] = {}
        for key, spec in aliases.items():
            name = _alias_name(key)
            if name in normalized_aliases:
                raise LLMConfigurationError(f"LLM alias {name!r} 重复")
            if not isinstance(spec, AliasSpec):
                raise TypeError(f"LLM alias {name!r} 必须使用 AliasSpec")
            normalized_aliases[name] = spec

        self._providers = MappingProxyType(normalized_providers)
        self._aliases = MappingProxyType(normalized_aliases)

    @property
    def providers(self) -> Mapping[str, BaseLLMProvider]:
        return self._providers

    @property
    def aliases(self) -> Mapping[str, AliasSpec]:
        return self._aliases

    def validate(self) -> LLMFactory:
        if not self._providers:
            raise LLMConfigurationError("LLM Factory 至少需要一个 provider")
        if not self._aliases:
            raise LLMConfigurationError("LLM Factory 至少需要一个 alias")
        for name, provider in self._providers.items():
            if not isinstance(provider, BaseLLMProvider):
                raise TypeError(f"provider {name!r} 未实现 BaseLLMProvider")
            if provider.name != name:
                raise LLMConfigurationError(
                    f"provider 映射键 {name!r} 与实例名 {provider.name!r} 不一致"
                )
            provider.validate()
        for alias in self._aliases:
            self.resolve(alias)
        return self

    def get_provider(self, provider: str) -> BaseLLMProvider:
        name = provider.strip()
        if not name:
            raise UnknownProviderError("provider 不能为空")
        instance = self._providers.get(name)
        if instance is None:
            raise UnknownProviderError(f"未知 provider: {name!r}")
        return instance

    def resolve(self, alias: str | LLMRole, **call_overrides: Any) -> ResolvedModel:
        name = _alias_name(alias)
        spec = self._aliases.get(name)
        if spec is None:
            raise UnknownAliasError(f"未知 LLM alias: {name!r}")
        provider = self.get_provider(spec.provider)
        model = provider.resolve_model(spec.model)
        capabilities = provider.capabilities_for(model)
        if spec.required_capabilities is not None and not capabilities.satisfies(
            spec.required_capabilities
        ):
            raise CapabilityMismatchError(
                f"alias {name!r} 的 provider/model {provider.name!r}/{model!r} "
                "不能满足声明的能力要求"
            )
        options = spec.merge_overrides(call_overrides)
        provider.validate_options(options)
        return ResolvedModel(
            alias=name,
            provider=provider.name,
            model=model,
            capabilities=capabilities,
            options=options,
        )

    def create(self, alias: str | LLMRole, **overrides: Any) -> Any:
        resolved = self.resolve(alias, **overrides)
        self._observe_creation(resolved)
        return self._providers[resolved.provider].create_model(
            resolved.model, **dict(resolved.options)
        )

    def capabilities(self, alias: str | LLMRole) -> ModelCapabilities:
        return self.resolve(alias).capabilities

    def supports(
        self,
        alias: str | LLMRole,
        required: ModelCapabilities,
    ) -> bool:
        return self.capabilities(alias).satisfies(required)

    def safe_info(self) -> Mapping[str, Any]:
        providers = {name: provider.safe_info() for name, provider in self._providers.items()}
        aliases = {
            name: {
                "provider": spec.provider,
                "model": spec.model,
                "override_keys": sorted(spec.overrides),
                "required_capabilities": (
                    None
                    if spec.required_capabilities is None
                    else spec.required_capabilities.safe_info()
                ),
            }
            for name, spec in self._aliases.items()
        }
        return {"providers": providers, "aliases": aliases}

    @staticmethod
    def _observe_creation(resolved: ResolvedModel) -> None:
        logger.info(
            "llm_model_create alias=%s provider=%s model=%s capabilities=%s",
            resolved.alias,
            resolved.provider,
            resolved.model,
            resolved.capabilities.safe_info(),
        )

    def __repr__(self) -> str:
        return f"LLMFactory({self.safe_info()!r})"
