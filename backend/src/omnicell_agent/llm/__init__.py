"""OmniCell-Agent 的统一 LLM provider、alias 与 factory 边界。"""

from .bootstrap import build_factory_from_env
from .errors import (
    CapabilityMismatchError,
    LLMConfigurationError,
    ModelNotAllowedError,
    UnknownAliasError,
    UnknownProviderError,
    UnknownProviderTypeError,
)
from .facade import (
    configure_default_factory,
    get_default_factory,
    get_llm_by_alias,
    reset_default_factory,
)
from .factory import LLMFactory
from .providers import BaseLLMProvider, OpenAICompatibleProvider
from .registry import ProviderRegistry, build_default_provider_registry
from .types import AliasSpec, LLMRole, ModelCapabilities, ResolvedModel

__all__ = [
    "AliasSpec",
    "BaseLLMProvider",
    "CapabilityMismatchError",
    "LLMConfigurationError",
    "LLMFactory",
    "LLMRole",
    "ModelCapabilities",
    "ModelNotAllowedError",
    "OpenAICompatibleProvider",
    "ProviderRegistry",
    "ResolvedModel",
    "UnknownAliasError",
    "UnknownProviderError",
    "UnknownProviderTypeError",
    "build_default_provider_registry",
    "build_factory_from_env",
    "configure_default_factory",
    "get_default_factory",
    "get_llm_by_alias",
    "reset_default_factory",
]
