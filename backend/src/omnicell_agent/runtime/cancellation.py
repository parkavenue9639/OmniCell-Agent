"""Thread-safe registration of active runtime cancellation callbacks."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar


RuntimeCancelCallback = Callable[[], object]


class RuntimeCancellationRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._callbacks: set[RuntimeCancelCallback] = set()

    @contextmanager
    def register(self, callback: RuntimeCancelCallback) -> Iterator[None]:
        with self._lock:
            self._callbacks.add(callback)
        try:
            yield
        finally:
            with self._lock:
                self._callbacks.discard(callback)

    async def cancel_all(self) -> None:
        with self._lock:
            callbacks = tuple(self._callbacks)
        if not callbacks:
            return
        results = await asyncio.gather(
            *(asyncio.to_thread(callback) for callback in callbacks),
            return_exceptions=True,
        )
        errors = [result for result in results if isinstance(result, BaseException)]
        if errors:
            raise ExceptionGroup("runtime cancellation callback 失败", errors)

    def cancel_all_blocking(self) -> None:
        """供隔离子进程的同步 signal handler 做尽力而为的优雅回收。"""

        with self._lock:
            callbacks = tuple(self._callbacks)
        errors: list[BaseException] = []
        for callback in callbacks:
            try:
                callback()
            except BaseException as exc:
                errors.append(exc)
        if errors:
            raise ExceptionGroup("runtime cancellation callback 失败", errors)


_CURRENT_REGISTRY: ContextVar[RuntimeCancellationRegistry | None] = ContextVar(
    "omnicell_runtime_cancellation_registry",
    default=None,
)


@contextmanager
def runtime_cancellation_scope(
    registry: RuntimeCancellationRegistry,
) -> Iterator[None]:
    token = _CURRENT_REGISTRY.set(registry)
    try:
        yield
    finally:
        _CURRENT_REGISTRY.reset(token)


@contextmanager
def register_runtime_cancel(callback: RuntimeCancelCallback) -> Iterator[None]:
    registry = _CURRENT_REGISTRY.get()
    if registry is None:
        yield
        return
    with registry.register(callback):
        yield


__all__ = [
    "RuntimeCancellationRegistry",
    "register_runtime_cancel",
    "runtime_cancellation_scope",
]
