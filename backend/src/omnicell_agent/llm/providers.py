"""模型供应商抽象与 OpenAI-compatible 初始实现。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from types import MappingProxyType
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from .errors import LLMConfigurationError, ModelNotAllowedError
from .types import ModelCapabilities


DEFAULT_OPENAI_COMPATIBLE_CAPABILITIES = ModelCapabilities(
    input_modalities=frozenset({"text"}),
    structured_output=True,
    streaming=True,
    tool_calling=True,
)

_PROTECTED_OPTIONS = frozenset(
    {
        "api_key",
        "async_client",
        "base_url",
        "client",
        "default_headers",
        "default_query",
        "extra_headers",
        "extra_query",
        "http_async_client",
        "http_client",
        "model",
        "model_kwargs",
        "model_name",
        "openai_api_base",
        "openai_api_key",
        "openai_organization",
        "openai_proxy",
        "organization",
        "root_async_client",
        "root_client",
        "websocket_base_url",
    }
)

_ALLOWED_OPTIONS = frozenset(
    {
        "cache",
        "callbacks",
        "context_management",
        "custom_get_token_ids",
        "disable_streaming",
        "disabled_params",
        "extra_body",
        "frequency_penalty",
        "include",
        "include_response_headers",
        "logit_bias",
        "logprobs",
        "max_retries",
        "max_tokens",
        "metadata",
        "n",
        "name",
        "output_version",
        "presence_penalty",
        "profile",
        "rate_limiter",
        "reasoning",
        "reasoning_effort",
        "request_timeout",
        "response_format",
        "seed",
        "service_tier",
        "stop",
        "store",
        "stream_usage",
        "streaming",
        "tags",
        "temperature",
        "tiktoken_model_name",
        "timeout",
        "top_logprobs",
        "top_p",
        "truncation",
        "use_previous_response_id",
        "use_responses_api",
        "verbose",
        "verbosity",
    }
)

_PROTECTED_NESTED_KEYS = _PROTECTED_OPTIONS | frozenset(
    {
        "access_key",
        "access_token",
        "auth",
        "authorization",
        "bearer",
        "cert",
        "cookie",
        "cookies",
        "credential",
        "credentials",
        "endpoint",
        "headers",
        "password",
        "private_key",
        "proxy",
        "proxy_authorization",
        "query",
        "refresh_token",
        "secret",
        "token",
        "url",
        "verify",
    }
)


def _normalized_option_key(value: object) -> str:
    return str(value).strip().lower().replace("-", "_").replace(".", "_")


def _protected_option_keys(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for raw_key, nested in value.items():
            key = _normalized_option_key(raw_key)
            if key in _PROTECTED_NESTED_KEYS:
                found.append(key)
                continue
            found.extend(_protected_option_keys(nested))
    elif isinstance(value, (list, tuple, set, frozenset)):
        for nested in value:
            found.extend(_protected_option_keys(nested))
    return found


def _safe_base_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return "<configured>"
    if not parsed.scheme or not parsed.hostname:
        return "<configured>"
    host = parsed.hostname
    if port is not None:
        host = f"{host}:{port}"
    return urlunsplit((parsed.scheme, host, parsed.path.rstrip("/"), "", ""))


def _validate_base_url(value: str, *, provider: str) -> None:
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError as exc:
        raise LLMConfigurationError(
            f"provider {provider!r} 的 base URL 无效"
        ) from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise LLMConfigurationError(
            f"provider {provider!r} 的 base URL 必须是有效的 HTTP(S) URL"
        )


class _SafeChatOpenAI(ChatOpenAI):
    """保留 ChatOpenAI 行为，同时避免其 repr 展开连接与 header 值。"""

    def __repr_args__(self):
        yield "model_name", self.model_name
        if self.openai_api_base:
            yield "base_url", _safe_base_url(str(self.openai_api_base))
        if self.default_headers:
            yield "default_header_names", sorted(self.default_headers)


class BaseLLMProvider(ABC):
    """Provider 实例的最小稳定合同。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """当前 provider 实例名。"""

    @property
    @abstractmethod
    def default_model(self) -> str:
        """`default` 应解析到的具体模型。"""

    @abstractmethod
    def validate(self) -> None:
        """启动期验证配置，失败时不得创建部分可用实例。"""

    @abstractmethod
    def resolve_model(self, model: str) -> str:
        """解析并验证模型名。"""

    @abstractmethod
    def capabilities_for(self, model: str) -> ModelCapabilities:
        """返回已解析模型的能力声明。"""

    @abstractmethod
    def create_model(self, model: str, **overrides: Any) -> Any:
        """创建统一 Chat Model 合同的实例。"""

    @abstractmethod
    def validate_options(self, options: Mapping[str, Any]) -> None:
        """在不构造模型的前提下校验 alias 或调用参数。"""

    @abstractmethod
    def safe_info(self) -> Mapping[str, Any]:
        """返回不含凭据与敏感配置的诊断信息。"""


class OpenAICompatibleProvider(BaseLLMProvider):
    """封装 `ChatOpenAI` 的 OpenAI-compatible provider。"""

    __slots__ = (
        "_name",
        "_default_model",
        "_base_url",
        "_api_key",
        "_default_headers",
        "_max_retries",
        "_timeout",
        "_model_allowlist",
        "_default_capabilities",
        "_model_capabilities",
        "_default_options",
    )

    def __init__(
        self,
        *,
        name: str,
        api_key: str,
        base_url: str,
        default_model: str,
        default_headers: Mapping[str, str] | None = None,
        max_retries: int = 3,
        timeout: float = 120.0,
        model_allowlist: frozenset[str] | set[str] | None = None,
        default_capabilities: ModelCapabilities = DEFAULT_OPENAI_COMPATIBLE_CAPABILITIES,
        model_capabilities: Mapping[str, ModelCapabilities] | None = None,
        default_options: Mapping[str, Any] | None = None,
    ) -> None:
        self._name = name.strip()
        self._default_model = default_model.strip()
        self._base_url = base_url.strip()
        self._api_key = SecretStr(api_key.strip())
        self._default_headers = MappingProxyType(dict(default_headers or {}))
        self._max_retries = max_retries
        self._timeout = timeout
        self._model_allowlist = (
            None if model_allowlist is None else frozenset(item.strip() for item in model_allowlist)
        )
        self._default_capabilities = default_capabilities
        self._model_capabilities = MappingProxyType(dict(model_capabilities or {}))
        self._default_options = MappingProxyType(dict(default_options or {}))
        self.validate()

    @property
    def name(self) -> str:
        return self._name

    @property
    def default_model(self) -> str:
        return self._default_model

    def validate(self) -> None:
        if not self._name:
            raise LLMConfigurationError("provider name 不能为空")
        if not self._default_model:
            raise LLMConfigurationError(f"provider {self._name!r} 的 default model 不能为空")
        if not self._base_url:
            raise LLMConfigurationError(f"provider {self._name!r} 的 base URL 不能为空")
        _validate_base_url(self._base_url, provider=self._name)
        if not self._api_key.get_secret_value():
            raise LLMConfigurationError(f"provider {self._name!r} 缺少 API key")
        if self._max_retries < 0:
            raise LLMConfigurationError(f"provider {self._name!r} 的 max_retries 不能为负数")
        if self._timeout <= 0:
            raise LLMConfigurationError(f"provider {self._name!r} 的 timeout 必须大于 0")
        self.validate_options({})
        if self._model_allowlist is not None:
            if "" in self._model_allowlist:
                raise LLMConfigurationError(f"provider {self._name!r} 的 model allowlist 含空值")
            if self._default_model not in self._model_allowlist:
                raise LLMConfigurationError(
                    f"provider {self._name!r} 的 default model {self._default_model!r} 不在 allowlist"
                )
        for model in self._model_capabilities:
            if not model.strip():
                raise LLMConfigurationError(f"provider {self._name!r} 的能力映射含空 model")
            if self._model_allowlist is not None and model not in self._model_allowlist:
                raise LLMConfigurationError(
                    f"provider {self._name!r} 的能力映射模型 {model!r} 不在 allowlist"
                )

    def resolve_model(self, model: str) -> str:
        candidate = model.strip()
        if not candidate:
            raise LLMConfigurationError(f"provider {self._name!r} 的 model 不能为空")
        resolved = self._default_model if candidate == "default" else candidate
        if self._model_allowlist is not None and resolved not in self._model_allowlist:
            raise ModelNotAllowedError(
                f"model {resolved!r} 不在 provider {self._name!r} 的显式 allowlist 中"
            )
        return resolved

    def capabilities_for(self, model: str) -> ModelCapabilities:
        resolved = self.resolve_model(model)
        return self._model_capabilities.get(resolved, self._default_capabilities)

    def create_model(self, model: str, **overrides: Any) -> ChatOpenAI:
        resolved = self.resolve_model(model)
        self.validate_options(overrides)
        merged = dict(self._default_options)
        merged.update(overrides)
        max_retries = merged.pop("max_retries", self._max_retries)
        timeout = merged.pop("timeout", merged.pop("request_timeout", self._timeout))
        return _SafeChatOpenAI(
            model=resolved,
            api_key=self._api_key,
            base_url=self._base_url,
            default_headers=self._default_headers or None,
            max_retries=max_retries,
            timeout=timeout,
            **merged,
        )

    def validate_options(self, options: Mapping[str, Any]) -> None:
        merged = dict(self._default_options)
        merged.update(options)
        protected_keys = sorted(set(_protected_option_keys(merged)))
        if protected_keys:
            raise LLMConfigurationError(
                f"provider {self._name!r} 的调用参数不得覆盖受保护字段 {protected_keys!r}"
            )
        unknown_options = [
            key for key in merged if not isinstance(key, str) or key not in _ALLOWED_OPTIONS
        ]
        if unknown_options:
            raise LLMConfigurationError(
                f"provider {self._name!r} 收到未审核或非规范的调用参数名"
            )
        if "timeout" in merged and "request_timeout" in merged:
            raise LLMConfigurationError("timeout 与 request_timeout 不能同时提供")
        max_retries = merged.get("max_retries", self._max_retries)
        if not isinstance(max_retries, int) or isinstance(max_retries, bool) or max_retries < 0:
            raise LLMConfigurationError("max_retries 必须是非负整数")
        timeout = merged.get("timeout", merged.get("request_timeout", self._timeout))
        if isinstance(timeout, (int, float)) and (
            isinstance(timeout, bool) or timeout <= 0
        ):
            raise LLMConfigurationError("timeout 必须大于 0")

    def safe_info(self) -> Mapping[str, Any]:
        return {
            "name": self._name,
            "type": "openai_compatible",
            "base_url": _safe_base_url(self._base_url),
            "default_model": self._default_model,
            "header_names": sorted(self._default_headers),
            "max_retries": self._max_retries,
            "timeout": self._timeout,
            "allowlist_enabled": self._model_allowlist is not None,
            "allowlist_size": None if self._model_allowlist is None else len(self._model_allowlist),
            "default_capabilities": self._default_capabilities.safe_info(),
        }

    def __repr__(self) -> str:
        return f"OpenAICompatibleProvider({self.safe_info()!r})"
