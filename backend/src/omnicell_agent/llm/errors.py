"""LLM Factory 的可诊断配置错误。"""


class LLMConfigurationError(ValueError):
    """LLM 配置无效且无法安全启动。"""


class UnknownAliasError(LLMConfigurationError):
    """请求了未注册的逻辑 alias。"""


class UnknownProviderError(LLMConfigurationError):
    """请求了未配置的 provider 实例。"""


class UnknownProviderTypeError(LLMConfigurationError):
    """请求了未注册的 provider 类型。"""


class ModelNotAllowedError(LLMConfigurationError):
    """模型不在 provider 的显式 allowlist 中。"""


class CapabilityMismatchError(LLMConfigurationError):
    """模型能力不能满足 alias 的声明要求。"""
