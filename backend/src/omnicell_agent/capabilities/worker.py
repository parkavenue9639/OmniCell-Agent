"""隔离 capability 子进程入口。"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import signal
import select
import sys
import threading
import time
from pathlib import Path
from typing import Any
from uuid import UUID

from omnicell_agent.runtime.cancellation import (
    RuntimeCancellationRegistry,
    runtime_cancellation_scope,
)

from .artifacts import ConversationArtifactStore
from .contracts import ArtifactRef
from .errors import CapabilityExecutionError, CapabilityInputError
from .registry import CapabilityContext
from omnicell_agent.runtime.activity import runtime_activity_scope


_PROTOCOL_VERSION = 1
_REQUEST_MAX_BYTES = 512 * 1024
_RESPONSE_MAX_BYTES = 4 * 1024 * 1024
_ACTIVITY_FRAME_MAX_BYTES = 56 * 1024


def _encode_activity(activity: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            activity,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def _largest_fitting_prefix(
    activity: dict[str, Any],
    *,
    field: str,
    value: str,
    minimum: int = 0,
) -> bytes:
    low = minimum
    high = len(value)
    best: bytes | None = None
    while low <= high:
        midpoint = (low + high) // 2
        activity[field] = value[:midpoint]
        encoded = _encode_activity(activity)
        if len(encoded) <= _ACTIVITY_FRAME_MAX_BYTES:
            best = encoded
            low = midpoint + 1
        else:
            high = midpoint - 1
    if best is None:
        raise ValueError("runtime activity frame 无法收敛到公开上限")
    return best


def _encode_runtime_activity_frame(activity: dict[str, Any]) -> bytes:
    """Serialize a trusted activity without letting disclosure break execution."""

    encoded = _encode_activity(activity)
    if len(encoded) <= _ACTIVITY_FRAME_MAX_BYTES:
        return encoded

    bounded = dict(activity)
    kind = bounded.get("kind")
    if kind == "runtime.command_started":
        script = str(bounded.get("script") or "")
        bounded["command_truncated"] = True
        bounded["script"] = ""
        if len(_encode_activity(bounded)) > _ACTIVITY_FRAME_MAX_BYTES:
            bounded["command"] = ["<runtime-command-truncated>"]
        return _largest_fitting_prefix(
            bounded,
            field="script",
            value=script,
        )
    if kind == "runtime.output":
        chunk = str(bounded.get("chunk") or "")
        bounded["truncated"] = True
        bounded["chunk"] = ""
        return _largest_fitting_prefix(
            bounded,
            field="chunk",
            value=chunk,
            minimum=1 if chunk else 0,
        )
    raise ValueError("runtime activity frame 超过上限")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--bootstrap", required=True)
    parser.add_argument("--response", required=True)
    parser.add_argument("--control-fd", required=True, type=int)
    parser.add_argument("--activity-fd", required=True, type=int)
    parser.add_argument("--watchdog-timeout", required=True, type=float)
    parser.add_argument("--watchdog-marker", required=True)
    return parser


def _load_bootstrap(target: str):
    module_name, separator, attribute = target.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("bootstrap target 必须使用 module:callable 格式")
    factory = getattr(importlib.import_module(module_name), attribute)
    if not callable(factory):
        raise TypeError("bootstrap target 必须可调用")
    return factory


def _read_request() -> dict[str, Any]:
    payload = sys.stdin.buffer.read(_REQUEST_MAX_BYTES + 1)
    if len(payload) > _REQUEST_MAX_BYTES:
        raise ValueError("capability process request 超过上限")
    request = json.loads(payload)
    if not isinstance(request, dict):
        raise TypeError("capability process request 必须是 object")
    if request.get("protocol_version") != _PROTOCOL_VERSION:
        raise ValueError("capability process 协议版本不匹配")
    invocation_id = str(request.get("invocation_id") or "")
    if invocation_id != os.environ.get("OMNICELL_CAPABILITY_INVOCATION_ID"):
        raise ValueError("capability process invocation identity 不匹配")
    return request


def _write_response(path: Path, payload: dict[str, Any]) -> None:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > _RESPONSE_MAX_BYTES:
        raise ValueError("capability process response 超过上限")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


class _RuntimeActivityPipe:
    def __init__(self, descriptor: int) -> None:
        self._descriptor = descriptor
        self._lock = threading.Lock()

    def __call__(self, activity: dict[str, Any]) -> None:
        encoded = _encode_runtime_activity_frame(activity)
        with self._lock:
            view = memoryview(encoded)
            while view:
                written = os.write(self._descriptor, view)
                view = view[written:]


def _run(
    bootstrap_target: str,
    *,
    control_fd: int,
    activity_fd: int,
    watchdog_timeout: float,
    watchdog_marker: Path,
) -> dict[str, Any]:
    request = _read_request()
    conversation_id = UUID(str(request["conversation_id"]))
    invocation_id = str(request["invocation_id"])
    workspace = Path(str(request["workspace"])).expanduser().resolve(strict=True)
    os.environ["OMNICELL_CONVERSATION_WORKSPACE"] = str(workspace)
    store = ConversationArtifactStore(
        conversation_id,
        workspace,
        invocation_id=invocation_id,
    )
    for raw_ref in request.get("trusted_artifacts") or []:
        store.register_trusted(ArtifactRef.model_validate(raw_ref))
    registry = RuntimeCancellationRegistry()

    terminating = False
    watchdog_expired = threading.Event()

    def watch_parent() -> None:
        deadline = (
            time.monotonic() + watchdog_timeout if watchdog_timeout > 0 else None
        )
        while True:
            wait_seconds = (
                max(deadline - time.monotonic(), 0) if deadline is not None else None
            )
            ready, _, _ = select.select([control_fd], [], [], wait_seconds)
            if not ready:
                watchdog_expired.set()
                watchdog_marker.write_text("expired", encoding="utf-8")
                os.kill(os.getpid(), signal.SIGTERM)
                time.sleep(1)
                os.killpg(os.getpgrp(), signal.SIGKILL)
                return
            payload = os.read(control_fd, 4096)
            if not payload:
                watchdog_expired.set()
                watchdog_marker.write_text("parent-eof", encoding="utf-8")
                os.kill(os.getpid(), signal.SIGTERM)
                time.sleep(1)
                os.killpg(os.getpgrp(), signal.SIGKILL)
                return
            if deadline is not None:
                deadline = time.monotonic() + watchdog_timeout

    watchdog = threading.Thread(
        target=watch_parent,
        name="omnicell-capability-parent-watchdog",
        daemon=True,
    )
    watchdog.start()

    def terminate(_signal_number, _frame) -> None:
        nonlocal terminating
        if terminating:
            raise SystemExit(143)
        terminating = True
        try:
            registry.cancel_all_blocking()
        finally:
            raise SystemExit(75 if watchdog_expired.is_set() else 143)

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    # Bootstrap may import user-selected capability modules. Keep that work under
    # the same parent/lease watchdog as the actual invocation so a dead owner
    # cannot leave an importing child alive indefinitely.
    layer = _load_bootstrap(bootstrap_target)()
    context = CapabilityContext(
        conversation_id=conversation_id,
        artifacts=store,
    )
    with runtime_activity_scope(_RuntimeActivityPipe(activity_fd)):
        with runtime_cancellation_scope(registry):
            result = layer.registry.invoke(
                str(request["capability"]),
                dict(request.get("arguments") or {}),
                context,
            )
    return {
        "protocol_version": _PROTOCOL_VERSION,
        "ok": True,
        "result": result.model_dump(mode="json"),
    }


def main() -> int:
    arguments = _parser().parse_args()
    response_path = Path(arguments.response)
    try:
        response = _run(
            arguments.bootstrap,
            control_fd=arguments.control_fd,
            activity_fd=arguments.activity_fd,
            watchdog_timeout=arguments.watchdog_timeout,
            watchdog_marker=Path(arguments.watchdog_marker),
        )
    except Exception as exc:
        public_message = (
            str(exc)[:2_000]
            if isinstance(exc, (CapabilityInputError, CapabilityExecutionError))
            else "isolated capability failed"
        )
        response = {
            "protocol_version": _PROTOCOL_VERSION,
            "ok": False,
            "error_type": type(exc).__name__,
            "message": public_message,
        }
    _write_response(response_path, response)
    return 0 if response.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
