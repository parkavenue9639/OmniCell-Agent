"""Graph A 基于 Local Docker Backend 的有状态 Python 会话。"""

from __future__ import annotations

import asyncio
import codecs
import os
import shutil
import stat
import threading
import time
import uuid
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

from .activity import (
    RuntimeActivitySink,
    current_runtime_activity_sink,
    sanitize_runtime_text,
)
from .docker_cli import OutputDelta
from .errors import DockerCommandTimeout, OutputLimitExceeded
from .local_docker import ExecutionResult, LocalDockerBackend
from .profile import RuntimeProfile


_PYTHON_RUNNER = r'''
import fcntl
import os
import pickle
import sys
import types

request_path, state_path, candidate_path = sys.argv[1:4]
with open(request_path, "r", encoding="utf-8") as handle:
    code = handle.read()
os.unlink(request_path)

# 子进程 exec 默认不能继承 Docker 控制连接；主 runner 自身仍可正常输出。
for descriptor in (sys.__stdout__.fileno(), sys.__stderr__.fileno()):
    flags = fcntl.fcntl(descriptor, fcntl.F_GETFD)
    fcntl.fcntl(descriptor, fcntl.F_SETFD, flags | fcntl.FD_CLOEXEC)

namespace = {"__name__": "__main__"}
try:
    with open(state_path, "rb") as handle:
        saved = pickle.load(handle)
except FileNotFoundError:
    saved = {}
if not isinstance(saved, dict):
    raise TypeError("persisted Python namespace is not a dict")
namespace.update(saved)

try:
    exec(compile(code, "<omnicell-runtime>", "exec"), namespace, namespace)
except SystemExit as exc:
    if exc.code not in (None, 0):
        raise

persisted = {}
for name, value in namespace.items():
    if (
        name == "__builtins__"
        or isinstance(
            value,
            (types.ModuleType, types.FunctionType, types.BuiltinFunctionType, type),
        )
        or type(value).__module__ == "__main__"
    ):
        continue
    candidate = {**persisted, name: value}
    try:
        pickle.dumps(candidate, protocol=pickle.HIGHEST_PROTOCOL)
    except BaseException:
        continue
    persisted = candidate

payload = pickle.dumps(persisted, protocol=pickle.HIGHEST_PROTOCOL)
temporary_path = f"{candidate_path}.{os.getpid()}.tmp"
try:
    with open(temporary_path, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary_path, candidate_path)
finally:
    try:
        os.unlink(temporary_path)
    except FileNotFoundError:
        pass
'''.strip()


_PROMOTE_STATE = r'''
import os
import sys

candidate_path, state_path = sys.argv[1:3]
os.replace(candidate_path, state_path)
'''.strip()


_DISCARD_STATE_CANDIDATE = r'''
import os
import sys

try:
    os.unlink(sys.argv[1])
except FileNotFoundError:
    pass
'''.strip()


class _AsyncLoopBridge:
    """让同步 Graph A 在一个固定 event loop 上使用异步 runtime。"""

    def __init__(self) -> None:
        self._ready = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread = threading.Thread(
            target=self._run_loop,
            name="omnicell-runtime-loop",
            daemon=True,
        )
        self._thread.start()
        self._ready.wait()

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        self._ready.set()
        loop.run_forever()
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()

    def run(self, coroutine: Coroutine[Any, Any, Any]) -> Any:
        loop = self._loop
        if loop is None or not self._thread.is_alive():
            coroutine.close()
            raise RuntimeError("runtime event loop 已关闭")
        return asyncio.run_coroutine_threadsafe(coroutine, loop).result()

    def close(self) -> None:
        loop = self._loop
        if loop is not None and self._thread.is_alive():
            loop.call_soon_threadsafe(loop.stop)
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                raise RuntimeError("runtime event loop 未能在 5 秒内关闭")
        self._loop = None


class LocalDockerPythonSession:
    """在 conversation workspace 上提供可回收的有状态 Python 执行会话。"""

    def __init__(
        self,
        *,
        host_workspace: str | Path | None = None,
        timeout_seconds: int = 120,
        profile: RuntimeProfile | None = None,
        backend: LocalDockerBackend | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须为正整数")
        if host_workspace is None:
            raise ValueError("Graph A Python session 必须提供 conversation host_workspace")
        self.host_workspace = Path(host_workspace).resolve()
        self.profile = profile or RuntimeProfile(
            name="graph-a-python",
            image=os.environ.get("OMNICELL_RUNTIME_IMAGE", "omnicell-worker:latest"),
            user="65532:65532",
            env={
                "HOME": "/tmp",
                "MPLCONFIGDIR": "/tmp/matplotlib",
                "PYTHONUNBUFFERED": "1",
            },
            allowed_commands=("python", "python3"),
        )
        self.timeout_seconds = timeout_seconds
        self._bridge = _AsyncLoopBridge()
        self._backend = backend or LocalDockerBackend(self.profile, self.host_workspace)
        token = uuid.uuid4().hex
        invocation_id = os.environ.get("OMNICELL_CAPABILITY_INVOCATION_ID", "").strip()
        if invocation_id:
            self._request_directory = (
                f".omnicell-invocations/{invocation_id}/"
                f".runtime/python-requests-{token}"
            )
        else:
            self._request_directory = f".omnicell-python-requests-{token}"
        self._state_name = "state.pickle"
        self._state_path = (
            f"{self.profile.workspace_root}/{self._request_directory}/{self._state_name}"
        )
        self._code_max_bytes = min(self.profile.write_max_bytes, 1024 * 1024)
        self._started = False
        self._closed = False
        self._poisoned = False
        self._state_lock = threading.RLock()

    def start(self) -> None:
        with self._state_lock:
            if self._closed:
                raise RuntimeError("Python session 已关闭，不能重新启动")
            if self._started:
                return
            try:
                self._bridge.run(self._backend.start())
                self._bridge.run(self._backend.ensure_dir(self._request_directory))
            except BaseException:
                self._bridge.run(self._backend.close())
                raise
            self._started = True

    def execute_code(self, code: str) -> dict[str, Any]:
        with self._state_lock:
            if not self._started:
                raise RuntimeError("Python session 尚未启动")
            if self._poisoned:
                raise RuntimeError("Python session 已 poisoned，拒绝复用不可信容器状态")
            code_bytes = code.encode("utf-8")
            if len(code_bytes) > self._code_max_bytes:
                raise ValueError(f"Python code 超过 {self._code_max_bytes} bytes 上限")

            request_name = f"request-{uuid.uuid4().hex}.py"
            candidate_name = f"candidate-{uuid.uuid4().hex}.pickle"
            request_path = f"{self._request_directory}/{request_name}"
            container_request_path = f"{self.profile.workspace_root}/{request_path}"
            candidate_path = (
                f"{self.profile.workspace_root}/{self._request_directory}/{candidate_name}"
            )
            try:
                return self._bridge.run(
                    self._execute_once(
                        code_bytes=code_bytes,
                        source_code=code,
                        request_path=request_path,
                        container_request_path=container_request_path,
                        candidate_path=candidate_path,
                        activity_sink=current_runtime_activity_sink(),
                    )
                )
            except OutputLimitExceeded:
                if not getattr(self._backend, "is_started", True):
                    self._poisoned = True
                raise
            except BaseException:
                self._poisoned = True
                raise

    def cancel_active(self) -> bool:
        """Thread-safe bridge used by product-level cancellation propagation."""

        return bool(self._bridge.run(self._backend.cancel_active()))

    async def _execute_once(
        self,
        *,
        code_bytes: bytes,
        source_code: str,
        request_path: str,
        container_request_path: str,
        candidate_path: str,
        activity_sink: RuntimeActivitySink | None,
    ) -> dict[str, Any]:
        await self._backend.write_bytes(request_path, code_bytes)
        command_id = uuid.uuid4().hex
        command = [
            "python",
            "-c",
            _PYTHON_RUNNER,
            container_request_path,
            self._state_path,
            candidate_path,
        ]
        host_workspace = str(self.host_workspace)
        # The actual argv below contains the trusted session runner and private
        # state paths. The public transcript exposes the user's container-level
        # operation instead; the bounded source is carried by the separate
        # ``script`` field.
        logical_command = ["python", "-c", "<agent-code>"]
        public_command: list[str] = []
        command_redacted = False
        command_truncated = False
        for token in logical_command:
            public_token, redacted, truncated = sanitize_runtime_text(
                token,
                host_workspace=host_workspace,
                max_bytes=8_000,
            )
            public_command.append(public_token)
            command_redacted = command_redacted or redacted
            command_truncated = command_truncated or truncated
        public_script, script_redacted, script_truncated = sanitize_runtime_text(
            source_code,
            host_workspace=host_workspace,
            max_bytes=24_000,
        )
        command_redacted = command_redacted or script_redacted
        command_truncated = command_truncated or script_truncated
        started_at = time.monotonic()
        output_indexes = {"stdout": 0, "stderr": 0}
        output_observed_bytes = {"stdout": 0, "stderr": 0}
        output_published_bytes = {"stdout": 0, "stderr": 0}
        output_truncated = {"stdout": False, "stderr": False}
        output_redacted = {"stdout": False, "stderr": False}
        output_buffers = {"stdout": "", "stderr": ""}
        output_encodings = {"stdout": "utf8", "stderr": "utf8"}
        output_decoders = {
            stream: codecs.getincrementaldecoder("utf-8")(errors="replace")
            for stream in ("stdout", "stderr")
        }

        def emit(record: dict[str, Any]) -> None:
            if activity_sink is not None:
                activity_sink(record)

        emit(
            {
                "kind": "runtime.command_started",
                "command_id": command_id,
                "backend": getattr(
                    self._backend,
                    "backend_name",
                    "local-docker",
                ),
                "command": public_command,
                "script": public_script,
                "workdir": self.profile.workspace_root,
                "command_truncated": command_truncated,
                "redacted": command_redacted,
            }
        )

        def publish_output(stream: str, decoded: str) -> None:
            if not decoded:
                return
            remaining = max(48_000 - output_published_bytes[stream], 0)
            if remaining == 0:
                output_truncated[stream] = True
                return
            public_text, redacted, truncated = sanitize_runtime_text(
                decoded,
                host_workspace=host_workspace,
                max_bytes=remaining,
            )
            output_redacted[stream] = output_redacted[stream] or redacted
            output_truncated[stream] = output_truncated[stream] or truncated
            output_published_bytes[stream] += len(public_text.encode("utf-8"))
            for offset in range(0, len(public_text), 8_000):
                chunk = public_text[offset : offset + 8_000]
                if not chunk:
                    continue
                index = output_indexes[stream]
                output_indexes[stream] += 1
                emit(
                    {
                        "kind": "runtime.output",
                        "command_id": command_id,
                        "stream": stream,
                        "index": index,
                        "chunk": chunk,
                        "encoding": output_encodings[stream],
                        "truncated": False,
                        "redacted": redacted,
                    }
                )

        def on_output(delta: OutputDelta) -> None:
            stream = delta.stream
            output_observed_bytes[stream] += len(delta.data)
            decoded = output_decoders[stream].decode(delta.data, final=False)
            if "\ufffd" in decoded:
                output_encodings[stream] = "utf8_replacement"
            output_buffers[stream] += decoded
            while "\n" in output_buffers[stream]:
                line, output_buffers[stream] = output_buffers[stream].split(
                    "\n",
                    1,
                )
                publish_output(stream, f"{line}\n")

        def flush_output() -> None:
            for stream in ("stdout", "stderr"):
                tail = output_decoders[stream].decode(b"", final=True)
                if "\ufffd" in tail:
                    output_encodings[stream] = "utf8_replacement"
                output_buffers[stream] += tail
                publish_output(stream, output_buffers[stream])
                output_buffers[stream] = ""

        def emit_completion(*, outcome: str, exit_code: int | None) -> None:
            flush_output()
            emit(
                {
                    "kind": "runtime.command_completed",
                    "command_id": command_id,
                    "outcome": outcome,
                    "exit_code": exit_code,
                    "duration_ms": max(
                        int((time.monotonic() - started_at) * 1_000),
                        0,
                    ),
                    "stdout_truncated": output_truncated["stdout"],
                    "stderr_truncated": output_truncated["stderr"],
                    "stdout_observed_bytes": output_observed_bytes["stdout"],
                    "stdout_published_bytes": output_published_bytes["stdout"],
                    "stderr_observed_bytes": output_observed_bytes["stderr"],
                    "stderr_published_bytes": output_published_bytes["stderr"],
                    "redacted": (
                        command_redacted
                        or output_redacted["stdout"]
                        or output_redacted["stderr"]
                    ),
                }
            )

        try:
            if activity_sink is None:
                result = await self._backend.execute(
                    command,
                    timeout=float(self.timeout_seconds),
                )
            else:
                result = await self._backend.execute(
                    command,
                    timeout=float(self.timeout_seconds),
                    on_output=on_output,
                )
        except DockerCommandTimeout:
            emit_completion(outcome="timeout", exit_code=None)
            if not getattr(self._backend, "is_started", True):
                raise
            await self._discard_candidate(candidate_path)
            return {
                "status": "timeout",
                "stdout": "",
                "stderr": (
                    "Python execution exceeded "
                    f"{float(self.timeout_seconds)} seconds"
                ),
                "display_data": [],
            }
        except asyncio.CancelledError:
            emit_completion(outcome="cancelled", exit_code=None)
            raise
        except BaseException as execution_exc:
            emit_completion(outcome="failed", exit_code=None)
            if getattr(self._backend, "is_started", True):
                try:
                    await self._discard_candidate(candidate_path)
                except BaseException as discard_exc:
                    discard_exc.add_note(
                        "execution also failed with "
                        f"{type(execution_exc).__name__}"
                    )
                    raise
            raise
        emit_completion(
            outcome="completed" if result.returncode == 0 else "failed",
            exit_code=result.returncode,
        )
        if result.returncode == 0:
            promoted = await self._backend.execute(
                [
                    "python",
                    "-c",
                    _PROMOTE_STATE,
                    candidate_path,
                    self._state_path,
                ],
                timeout=float(self.timeout_seconds),
            )
            if promoted.returncode != 0:
                raise RuntimeError(
                    "Python namespace snapshot 提交失败："
                    f"{promoted.stderr_text.strip() or promoted.returncode}"
                )
        return self._result_payload(result)

    async def _discard_candidate(self, candidate_path: str) -> None:
        discarded = await self._backend.execute(
            [
                "python",
                "-c",
                _DISCARD_STATE_CANDIDATE,
                candidate_path,
            ],
            timeout=float(self.timeout_seconds),
        )
        if discarded.returncode != 0:
            raise RuntimeError(
                "Python namespace candidate 清理失败："
                f"{discarded.stderr_text.strip() or discarded.returncode}"
            )

    @staticmethod
    def _result_payload(result: ExecutionResult) -> dict[str, Any]:
        return {
            "status": "success" if result.returncode == 0 else "error",
            "stdout": result.stdout_text,
            "stderr": result.stderr_text,
            "display_data": [],
        }

    def metadata(self) -> dict[str, Any]:
        return {
            **dict(self._backend.metadata()),
            "python_session": {
                "process_model": "per-call",
                "completion_boundary": "docker-exec-returncode",
                "namespace_persistence": "conversation-workspace-pickle",
            },
        }

    def cleanup(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            try:
                self._bridge.run(self._backend.close())
            except BaseException:
                # backend 会保留未成功删除的 owned identity；保留 event loop 供调用方重试。
                self._poisoned = True
                raise
            try:
                self._cleanup_request_directory()
            except BaseException:
                # host 状态目录也是 session 生命周期的一部分。清理失败时保留
                # bridge 与未关闭状态，让上层能够针对同一 owned scope 重试。
                self._poisoned = True
                raise
            try:
                self._bridge.close()
            except BaseException:
                self._poisoned = True
                raise
            self._started = False
            self._closed = True

    def _cleanup_request_directory(self) -> None:
        """容器关闭后删除本 session 唯一拥有的内部状态目录。"""

        directory = self.host_workspace / self._request_directory
        try:
            directory_mode = directory.lstat().st_mode
        except FileNotFoundError:
            return
        if stat.S_ISLNK(directory_mode):
            directory.unlink()
            return
        if not stat.S_ISDIR(directory_mode):
            directory.unlink()
            return

        # 目录名由 session 随机生成且整棵树都归本实例独占；rmtree 不跟随
        # 树内符号链接，可完整回收用户代码意外创建的嵌套状态。
        shutil.rmtree(directory)


__all__ = ["LocalDockerPythonSession"]
