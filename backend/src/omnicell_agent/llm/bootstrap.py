"""从环境配置构建进程级 LLM Factory。"""

from __future__ import annotations

import os
from typing import Mapping

from omnicell_agent.core.environment import load_project_environment

from .errors import LLMConfigurationError, UnknownProviderTypeError
from .factory import LLMFactory
from .registry import build_default_provider_registry
from .types import AliasSpec, LLMRole, ModelCapabilities

_PROVIDER_ENV = {
    "openrouter": {
        "api_key": "OPENROUTER_API_KEY",
        "base_url": "OPENROUTER_BASE_URL",
        "default_model": "DEFAULT_OPENROUTER_MODEL",
        "base_url_default": "https://openrouter.ai/api/v1",
        "model_default": "openai/gpt-4o-mini",
    },
    "onerouter": {
        "api_key": "ONEROUTER_API_KEY",
        "base_url": "ONEROUTER_BASE_URL",
        "default_model": "DEFAULT_ONEROUTER_MODEL",
        "base_url_default": "https://llm.onerouter.pro/v1",
        "model_default": "gemini-2.5-flash",
    },
}

_ROLE_REQUIREMENTS = {
    LLMRole.AGENT_PRIMARY: ModelCapabilities(streaming=True, tool_calling=True),
    LLMRole.FAST_ROUTER: ModelCapabilities(structured_output=True),
    LLMRole.CODE_GENERATION: ModelCapabilities(),
    LLMRole.ANNOTATION: ModelCapabilities(structured_output=True),
    LLMRole.VALIDATION: ModelCapabilities(structured_output=True),
    LLMRole.SUMMARY: ModelCapabilities(),
    LLMRole.VISION: ModelCapabilities(
        input_modalities=frozenset({"text", "image"}),
        structured_output=True,
    ),
}


def _configured_value(
    environ: Mapping[str, str],
    key: str,
    *,
    default: str | None = None,
) -> str:
    value = environ.get(key, default)
    if value is None or not value.strip():
        raise LLMConfigurationError(f"环境变量 {key} 未配置或为空")
    return value.strip()


def _parse_target(target: str, *, source: str) -> tuple[str, str]:
    value = target.strip()
    if not value:
        raise LLMConfigurationError(f"{source} 的 LLM target 不能为空")
    if "/" not in value:
        raise LLMConfigurationError(f"{source} 必须使用 provider/model 格式")
    provider, model = value.split("/", 1)
    provider = provider.strip()
    model = model.strip()
    if not provider or not model:
        raise LLMConfigurationError(f"{source} 必须包含非空 provider 和 model")
    return provider, model


def _parse_modalities(raw: str, *, source: str) -> frozenset[str]:
    modalities = frozenset(item.strip().lower() for item in raw.split(",") if item.strip())
    if not modalities:
        raise LLMConfigurationError(f"{source} 必须声明至少一种输入模态")
    return modalities


def build_factory_from_env(environ: Mapping[str, str] | None = None) -> LLMFactory:
    """解析环境并完成启动期验证；不会创建模型或发起网络请求。"""

    if environ is None:
        load_project_environment()
        source: Mapping[str, str] = os.environ
    else:
        source = environ
    default_target = _configured_value(
        source,
        "OMNICELL_LLM_DEFAULT",
        default="onerouter/default",
    )
    default_provider, default_model = _parse_target(
        default_target, source="OMNICELL_LLM_DEFAULT"
    )

    aliases: dict[str, AliasSpec] = {
        "default": AliasSpec(provider=default_provider, model=default_model)
    }
    for role in LLMRole:
        key = f"OMNICELL_LLM_{role.value.upper()}"
        target = source.get(key, "").strip() or default_target
        provider, model = _parse_target(target, source=key)
        aliases[role.value] = AliasSpec(
            provider=provider,
            model=model,
            required_capabilities=_ROLE_REQUIREMENTS[role],
        )

    vision_modalities = _parse_modalities(
        source.get("OMNICELL_LLM_VISION_INPUT_MODALITIES", "text,image"),
        source="OMNICELL_LLM_VISION_INPUT_MODALITIES",
    )

    registry = build_default_provider_registry()
    providers = {}
    for provider_name in sorted({spec.provider for spec in aliases.values()}):
        env_spec = _PROVIDER_ENV.get(provider_name)
        if env_spec is None:
            raise UnknownProviderTypeError(
                f"环境 alias 引用了不支持的 provider: {provider_name!r}"
            )
        api_key_name = env_spec["api_key"]
        api_key = _configured_value(source, api_key_name)
        base_url = _configured_value(
            source,
            env_spec["base_url"],
            default=env_spec["base_url_default"],
        )
        default_model_name = _configured_value(
            source,
            env_spec["default_model"],
            default=env_spec["model_default"],
        )
        default_headers = None
        if provider_name == "openrouter":
            default_headers = {
                "HTTP-Referer": source.get(
                    "APP_REFERER", "https://github.com/OmniCell-Agent"
                ),
                "X-Title": source.get("APP_TITLE", "OmniCell-Agent"),
            }
        model_capabilities = None
        vision_spec = aliases[LLMRole.VISION.value]
        if vision_spec.provider == provider_name:
            vision_model = (
                default_model_name if vision_spec.model == "default" else vision_spec.model
            )
            model_capabilities = {
                vision_model: ModelCapabilities(
                    input_modalities=vision_modalities,
                    structured_output=True,
                    streaming=True,
                    tool_calling=True,
                )
            }
        providers[provider_name] = registry.create(
            "openai_compatible",
            name=provider_name,
            api_key=api_key,
            base_url=base_url,
            default_model=default_model_name,
            default_headers=default_headers,
            model_capabilities=model_capabilities,
        )

    return LLMFactory(providers=providers, aliases=aliases).validate()
