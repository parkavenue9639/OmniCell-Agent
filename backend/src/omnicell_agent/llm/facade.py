"""组合根使用的进程默认 Factory facade。"""

from __future__ import annotations

from threading import RLock
from typing import Any

from .bootstrap import build_factory_from_env
from .factory import LLMFactory
from .types import LLMRole

_factory_lock = RLock()
_default_factory: LLMFactory | None = None


def configure_default_factory(factory: LLMFactory) -> LLMFactory:
    """显式安装进程默认 factory，并先执行完整配置校验。"""

    if not isinstance(factory, LLMFactory):
        raise TypeError("default factory 必须是 LLMFactory")
    factory.validate()
    global _default_factory
    with _factory_lock:
        _default_factory = factory
    return factory


def reset_default_factory() -> None:
    """清除默认 factory，主要用于进程关闭或测试隔离。"""

    global _default_factory
    with _factory_lock:
        _default_factory = None


def get_default_factory() -> LLMFactory:
    """首次使用时才从环境构建；import 阶段没有模型实例化。"""

    global _default_factory
    with _factory_lock:
        if _default_factory is None:
            _default_factory = build_factory_from_env()
        return _default_factory


def get_llm_by_alias(alias: str | LLMRole, **overrides: Any) -> Any:
    return get_default_factory().create(alias, **overrides)
