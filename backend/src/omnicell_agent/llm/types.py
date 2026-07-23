"""LLM Factory 的稳定值对象。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping


class LLMRole(StrEnum):
    """领域代码可依赖的逻辑模型角色。"""

    AGENT_PRIMARY = "agent_primary"
    FAST_ROUTER = "fast_router"
    CODE_GENERATION = "code_generation"
    ANNOTATION = "annotation"
    VALIDATION = "validation"
    SUMMARY = "summary"
    VISION = "vision"


def _immutable_mapping(values: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return MappingProxyType(dict(values or {}))


@dataclass(frozen=True, slots=True)
class ModelCapabilities:
    """模型能力声明；未声明的布尔能力按不支持处理。"""

    input_modalities: frozenset[str] = field(default_factory=lambda: frozenset({"text"}))
    structured_output: bool = False
    streaming: bool = False
    tool_calling: bool = False

    def __post_init__(self) -> None:
        modalities = frozenset(item.strip().lower() for item in self.input_modalities if item.strip())
        if not modalities:
            raise ValueError("input_modalities 不能为空")
        object.__setattr__(self, "input_modalities", modalities)

    def satisfies(self, required: ModelCapabilities) -> bool:
        """判断当前能力是否覆盖所需能力。"""

        return (
            required.input_modalities.issubset(self.input_modalities)
            and (not required.structured_output or self.structured_output)
            and (not required.streaming or self.streaming)
            and (not required.tool_calling or self.tool_calling)
        )

    def safe_info(self) -> dict[str, Any]:
        return {
            "input_modalities": sorted(self.input_modalities),
            "structured_output": self.structured_output,
            "streaming": self.streaming,
            "tool_calling": self.tool_calling,
        }


@dataclass(frozen=True, slots=True)
class AliasSpec:
    """逻辑 alias 到 provider/model 的不可变映射。"""

    provider: str
    model: str = "default"
    overrides: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)
    required_capabilities: ModelCapabilities | None = None

    def __post_init__(self) -> None:
        provider = self.provider.strip()
        model = self.model.strip()
        if not provider:
            raise ValueError("alias provider 不能为空")
        if not model:
            raise ValueError("alias model 不能为空")
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "overrides", _immutable_mapping(self.overrides))

    def merge_overrides(self, call_overrides: Mapping[str, Any]) -> Mapping[str, Any]:
        merged = dict(self.overrides)
        merged.update(call_overrides)
        return _immutable_mapping(merged)

    def __repr__(self) -> str:
        return (
            "AliasSpec("
            f"provider={self.provider!r}, model={self.model!r}, "
            f"override_keys={sorted(self.overrides)!r}, "
            f"required_capabilities={self.required_capabilities!r})"
        )


@dataclass(frozen=True, slots=True)
class ResolvedModel:
    """一次 alias 解析的安全、不可变结果。"""

    alias: str
    provider: str
    model: str
    capabilities: ModelCapabilities
    options: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.provider.strip():
            raise ValueError("resolved provider 不能为空")
        if not self.model.strip():
            raise ValueError("resolved model 不能为空")
        object.__setattr__(self, "options", _immutable_mapping(self.options))

    def safe_info(self) -> dict[str, Any]:
        return {
            "alias": self.alias,
            "provider": self.provider,
            "model": self.model,
            "capabilities": self.capabilities.safe_info(),
            "option_keys": sorted(self.options),
        }

    def __repr__(self) -> str:
        return f"ResolvedModel({self.safe_info()!r})"
