from __future__ import annotations

import dataclasses
import datetime as dt
import enum
import pathlib
import uuid
from collections.abc import Mapping, Sequence, Set
from decimal import Decimal
from typing import Any

from langgraph.types import Send


_FORBIDDEN_MODULE_PREFIXES = (
    "anndata",
    "numpy",
    "pandas",
    "polars",
    "scipy.sparse",
)


class PersistencePayloadError(ValueError):
    pass


class ForbiddenPersistenceTypeError(PersistencePayloadError):
    pass


class PersistencePayloadTooLargeError(PersistencePayloadError):
    pass


def _measure(value: Any, *, seen: set[int], depth: int) -> int:
    if depth > 32:
        raise PersistencePayloadError("持久化 payload 嵌套层级超过 32")
    if value is None or isinstance(value, (bool, int, float)):
        return 8
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    if isinstance(value, bytes):
        raise ForbiddenPersistenceTypeError("二进制内容必须保存为 artifact，而不是数据库 payload")
    if isinstance(value, (dt.date, dt.datetime, dt.time, uuid.UUID, pathlib.Path, Decimal, enum.Enum)):
        return len(str(value).encode("utf-8"))
    if isinstance(value, Send):
        # Send is LangGraph's explicit fan-out control envelope.  Its payload
        # still traverses the same recursive guard; arbitrary LangGraph/module
        # objects remain fail-closed.
        return _measure(
            {"node": value.node, "arg": value.arg},
            seen=seen,
            depth=depth + 1,
        )

    module = type(value).__module__
    if module.startswith(_FORBIDDEN_MODULE_PREFIXES):
        raise ForbiddenPersistenceTypeError(
            f"{module}.{type(value).__name__} 必须保存到 workspace/artifact 层"
        )

    object_id = id(value)
    if object_id in seen:
        raise PersistencePayloadError("持久化 payload 不允许循环引用")

    if hasattr(value, "model_dump"):
        seen.add(object_id)
        try:
            return _measure(value.model_dump(), seen=seen, depth=depth + 1)
        finally:
            seen.remove(object_id)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        seen.add(object_id)
        try:
            return _measure(dataclasses.asdict(value), seen=seen, depth=depth + 1)
        finally:
            seen.remove(object_id)
    if isinstance(value, Mapping):
        seen.add(object_id)
        try:
            total = 0
            for key, item in value.items():
                if not isinstance(key, (str, int, float, bool, uuid.UUID)):
                    raise PersistencePayloadError(
                        f"持久化 payload 的键必须是标量，当前为 {type(key).__name__}"
                    )
                total += _measure(str(key), seen=seen, depth=depth + 1)
                total += _measure(item, seen=seen, depth=depth + 1)
            return total
        finally:
            seen.remove(object_id)
    if isinstance(value, (Sequence, Set)) and not isinstance(value, (str, bytes, bytearray)):
        seen.add(object_id)
        try:
            return sum(_measure(item, seen=seen, depth=depth + 1) for item in value)
        finally:
            seen.remove(object_id)

    raise ForbiddenPersistenceTypeError(
        f"不支持持久化类型 {module}.{type(value).__name__}；请转换为结构化值或 artifact 引用"
    )


def ensure_payload_safe(value: Any, *, max_bytes: int, label: str) -> int:
    """拒绝大型科学对象、二进制内容、循环结构和超限控制 payload。"""

    if max_bytes < 1:
        raise ValueError("max_bytes 必须大于 0")
    measured = _measure(value, seen=set(), depth=0)
    if measured > max_bytes:
        raise PersistencePayloadTooLargeError(
            f"{label} 估算大小 {measured} bytes，超过上限 {max_bytes} bytes"
        )
    return measured
