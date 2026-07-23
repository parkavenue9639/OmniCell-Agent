"""基于异步 Docker CLI 的本地隔离执行后端。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shlex
import uuid
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .docker_cli import DockerCLI, DockerCommandResult, OutputCallback
from .errors import (
    CommandNotAllowedError,
    DockerCommandError,
    ImageUnavailableError,
    RuntimeBackendError,
    RuntimeNotStartedError,
)
from .paths import WorkspacePathResolver
from .output_policy import OutputQuota, OutputQuotaViolation, scan_output_tree
from .profile import PullPolicy, RuntimeAuthorization, RuntimeProfile


_PROCESS_SNAPSHOT_SCRIPT = r"""
import json
import os

def identity(pid):
    try:
        with open(f"/proc/{pid}/stat", "r", encoding="utf-8") as handle:
            raw = handle.read()
        tail = raw[raw.rfind(") ") + 2:].split()
        return tail[19]
    except (FileNotFoundError, IndexError, PermissionError, ProcessLookupError):
        return None

self_pid = os.getpid()
snapshot = {}
for name in os.listdir("/proc"):
    if not name.isdigit():
        continue
    pid = int(name)
    if pid in {1, self_pid}:
        continue
    marker = identity(pid)
    if marker is not None:
        snapshot[name] = marker
print(json.dumps(snapshot, sort_keys=True, separators=(",", ":")))
""".strip()


_PROCESS_CLEANUP_SCRIPT = r"""
import json
import os
import signal
import sys
import time

baseline = json.loads(sys.argv[1])
self_pid = os.getpid()

def identity(pid):
    try:
        with open(f"/proc/{pid}/stat", "r", encoding="utf-8") as handle:
            raw = handle.read()
        tail = raw[raw.rfind(") ") + 2:].split()
        return tail[19]
    except (FileNotFoundError, IndexError, PermissionError, ProcessLookupError):
        return None

def victims():
    found = []
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        pid = int(name)
        if pid in {1, self_pid}:
            continue
        marker = identity(pid)
        if marker is not None and baseline.get(name) != marker:
            found.append(pid)
    return found

for sig in (signal.SIGTERM, signal.SIGKILL):
    for _ in range(4):
        current = victims()
        if not current:
            break
        for pid in current:
            try:
                os.kill(pid, sig)
            except (PermissionError, ProcessLookupError):
                pass
        time.sleep(0.05)

remaining = victims()
if remaining:
    print(json.dumps({"remaining_pids": remaining}), file=sys.stderr)
    raise SystemExit(70)
""".strip()


_CONTAINER_IDLE_SCRIPT = r"""
import signal
import threading

stopped = threading.Event()
signal.signal(signal.SIGTERM, lambda *_: stopped.set())
signal.signal(signal.SIGINT, lambda *_: stopped.set())
stopped.wait()
""".strip()

_INVOCATION_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_IMMUTABLE_CONTAINER_ID_PATTERN = re.compile(r"^[0-9a-f]{64}$")


_FILE_SCRIPT = r"""
import fnmatch
import json
import os
import re
import stat
import sys

operation, root, target = sys.argv[1:4]
root = os.path.realpath(root)
directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW

def target_parts():
    relative = os.path.relpath(target, root)
    if relative == ".":
        return []
    if os.path.isabs(relative) or relative == ".." or relative.startswith("../"):
        raise PermissionError("path escapes workspace")
    parts = relative.split(os.sep)
    if any(part in {"", ".", ".."} for part in parts):
        raise PermissionError("path contains unsafe component")
    return parts

def open_dir(parts, *, create=False):
    descriptor = os.open(root, directory_flags)
    try:
        for part in parts:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
            next_descriptor = os.open(part, directory_flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise

def open_parent(parts, *, create=False):
    if not parts:
        raise PermissionError("operation requires a path below workspace root")
    return open_dir(parts[:-1], create=create), parts[-1]

def walk_files(base_parts):
    base_descriptor = open_dir(base_parts)

    def visit(descriptor, relative_parts):
        with os.scandir(descriptor) as iterator:
            entries = sorted(iterator, key=lambda entry: entry.name)
        for entry in entries:
            name = entry.name
            if entry.is_symlink():
                continue
            relative = relative_parts + [name]
            try:
                if entry.is_dir(follow_symlinks=False):
                    child = os.open(name, directory_flags, dir_fd=descriptor)
                    try:
                        yield from visit(child, relative)
                    finally:
                        os.close(child)
                elif entry.is_file(follow_symlinks=False):
                    yield descriptor, name, relative
            except (FileNotFoundError, NotADirectoryError, PermissionError):
                continue

    try:
        yield from visit(base_descriptor, list(base_parts))
    finally:
        os.close(base_descriptor)

parts = target_parts()

if operation == "write":
    parent, name = open_parent(parts, create=True)
    try:
        descriptor = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent,
        )
    finally:
        os.close(parent)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(sys.stdin.buffer.read())
elif operation == "read":
    limit = int(sys.argv[4])
    parent, name = open_parent(parts)
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
    finally:
        os.close(parent)
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise PermissionError("read target must be a regular file")
    with os.fdopen(descriptor, "rb") as handle:
        payload = handle.read(limit + 1)
    if len(payload) > limit:
        raise ValueError("file exceeds configured read limit")
    sys.stdout.buffer.write(payload)
elif operation == "mkdir":
    descriptor = open_dir(parts, create=True)
    os.close(descriptor)
elif operation == "list":
    rows = ["/".join(relative) for _, _, relative in walk_files(parts)]
    print(json.dumps(sorted(rows), ensure_ascii=False))
elif operation == "glob":
    pattern = sys.argv[4]
    rows = []
    for _, _, relative in walk_files(parts):
        relative_to_target = "/".join(relative[len(parts):])
        if fnmatch.fnmatch(relative_to_target, pattern):
            rows.append("/".join(relative))
    print(json.dumps(sorted(set(rows)), ensure_ascii=False))
elif operation == "grep":
    expression = re.compile(sys.argv[4])
    max_matches = int(sys.argv[5])
    rows = []
    for parent, name, relative in walk_files(parts):
        try:
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                os.close(descriptor)
                continue
            with os.fdopen(descriptor, "r", encoding="utf-8", errors="replace") as handle:
                for line_number, line in enumerate(handle, 1):
                    if expression.search(line):
                        rows.append(["/".join(relative), line_number, line.rstrip("\n")])
                        if len(rows) >= max_matches:
                            print(json.dumps(rows, ensure_ascii=False))
                            raise SystemExit(0)
        except (FileNotFoundError, IsADirectoryError, PermissionError):
            pass
    print(json.dumps(rows, ensure_ascii=False))
else:
    raise ValueError("unknown file operation")
""".strip()


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """容器命令执行结果。"""

    returncode: int
    stdout: bytes
    stderr: bytes

    @property
    def stdout_text(self) -> str:
        return self.stdout.decode("utf-8", errors="replace")

    @property
    def stderr_text(self) -> str:
        return self.stderr.decode("utf-8", errors="replace")


@dataclass(frozen=True, slots=True)
class GrepMatch:
    """workspace 文本搜索命中。"""

    path: str
    line: int
    text: str


class LocalDockerBackend:
    """一个 conversation workspace 对应一个可替换的本地容器。"""

    backend_name = "local-docker-cli"

    def __init__(
        self,
        profile: RuntimeProfile,
        host_workspace: str | Path,
        *,
        docker: DockerCLI | None = None,
        authorization: RuntimeAuthorization | None = None,
    ):
        self.profile = profile
        self.authorization = authorization or RuntimeAuthorization()
        if self.profile.network != "none" and not self.authorization.allow_network:
            raise CommandNotAllowedError("network profile 需要 Tool policy 显式授权")
        self._host_workspace = Path(host_workspace).expanduser().resolve(strict=False)
        if any(character in str(self._host_workspace) for character in (",", "\x00", "\n", "\r")):
            raise RuntimeBackendError("宿主 workspace 路径包含 Docker mount 不支持的字符")
        self._workspace_identity = "workspace-" + hashlib.sha256(
            str(self._host_workspace).encode("utf-8")
        ).hexdigest()[:16]
        raw_invocation_id = os.environ.get(
            "OMNICELL_CAPABILITY_INVOCATION_ID", ""
        ).strip()
        if raw_invocation_id and not _INVOCATION_ID_PATTERN.fullmatch(
            raw_invocation_id
        ):
            raise RuntimeBackendError("capability invocation identity 非法")
        self._invocation_id = raw_invocation_id or None
        self._invocation_output = (
            self._host_workspace
            / ".omnicell-invocations"
            / self._invocation_id
            if self._invocation_id is not None
            else None
        )
        self._output_quota = OutputQuota(
            max_files=profile.output_max_files,
            file_max_bytes=profile.output_file_max_bytes,
            total_max_bytes=profile.output_total_max_bytes,
        )
        raw_ownership_file = os.environ.get(
            "OMNICELL_RUNTIME_OWNERSHIP_FILE", ""
        ).strip()
        ownership_file = Path(raw_ownership_file) if raw_ownership_file else None
        if ownership_file is not None and not ownership_file.is_absolute():
            raise RuntimeBackendError("runtime ownership file 必须是绝对路径")
        self._ownership_file = (
            ownership_file.resolve(strict=False)
            if ownership_file is not None
            else None
        )
        if self._ownership_file is not None:
            try:
                self._ownership_file.relative_to(self._host_workspace)
            except ValueError:
                pass
            else:
                raise RuntimeBackendError(
                    "runtime ownership file 必须位于容器不可见的控制面"
                )
        self._paths = WorkspacePathResolver(profile.workspace_root)
        self._docker = docker or DockerCLI()
        self._container_id: str | None = None
        self._container_identity_confirmed = False
        self._started = False
        self._image_identity: str | None = None
        self._state_lock = asyncio.Lock()
        self._operation_lock = asyncio.Lock()
        self._active_task: asyncio.Task[Any] | None = None
        self._closing_requests = 0

    @property
    def container_id(self) -> str | None:
        return self._container_id

    @property
    def image_identity(self) -> str | None:
        return self._image_identity

    @property
    def is_started(self) -> bool:
        return self._started

    async def __aenter__(self) -> "LocalDockerBackend":
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def start(self) -> None:
        """延迟、幂等地连接或创建容器。"""

        async with self._state_lock:
            if self._closing_requests:
                raise RuntimeBackendError("Local Docker Backend 正在关闭，不能启动")
            if self._started:
                return
            if self._container_id is not None:
                await self._remove_owned_container(
                    self._container_id,
                    missing_is_safe=self._container_identity_confirmed,
                )
                self._container_id = None
                self._container_identity_confirmed = False
                self._image_identity = None
                self._clear_ownership_claim()
            await asyncio.to_thread(self._host_workspace.mkdir, parents=True, exist_ok=True)
            self._host_workspace = self._host_workspace.resolve(strict=True)
            if self._invocation_output is not None:
                self._invocation_output = (
                    self._host_workspace
                    / ".omnicell-invocations"
                    / self._invocation_id  # type: ignore[operator]
                )
                await asyncio.to_thread(self._prepare_invocation_output)

            inspect_payload, identity = await self._ensure_image()
            del inspect_payload
            name = f"omnicell-{self.profile.name}-{uuid.uuid4().hex[:12]}"
            args = self._docker_run_args(name=name, image_identity=identity)
            self._container_id = name
            self._container_identity_confirmed = False
            self._write_ownership_claim(name, state="provisional")
            try:
                result = await self._docker.run(
                    args,
                    timeout=60,
                    stdout_max_bytes=4096,
                    stderr_max_bytes=self.profile.stderr_max_bytes,
                )
                container_id = result.stdout.decode("utf-8", errors="replace").strip()
                if not container_id:
                    raise RuntimeBackendError("docker run 未返回 container id")
            except BaseException as exc:
                try:
                    await asyncio.shield(
                        self._remove_owned_container(
                            name,
                            missing_is_safe=False,
                        )
                    )
                except BaseException as cleanup_exc:
                    exc.add_note(f"provisional container cleanup failed: {type(cleanup_exc).__name__}")
                else:
                    self._container_id = None
                    self._container_identity_confirmed = False
                    self._clear_ownership_claim()
                raise
            self._container_id = container_id
            self._container_identity_confirmed = True
            self._write_ownership_claim(container_id, state="confirmed")
            self._image_identity = identity
            self._started = True

    async def close(self) -> None:
        """取消活跃执行，等待文件操作收尾，并回收本后端拥有的容器。"""

        if self._active_task is asyncio.current_task():
            raise RuntimeBackendError("不能从当前活跃 execution 内关闭 runtime")
        self._closing_requests += 1
        try:
            cancellation_failure: BaseException | None = None
            try:
                await self.cancel_active()
            except BaseException as exc:
                cancellation_failure = exc
            async with self._operation_lock:
                async with self._state_lock:
                    if self._container_id is None:
                        self._started = False
                        if cancellation_failure is not None:
                            raise cancellation_failure
                        return
                    container_id = self._container_id
                    # 一旦进入回收流程，该容器便不再允许承载新执行。若删除失败，
                    # 保留 identity 只用于后续 start/close 重试删除，不能继续复用。
                    self._started = False
                    await self._remove_owned_container(
                        container_id,
                        missing_is_safe=self._container_identity_confirmed,
                    )
                    self._container_id = None
                    self._container_identity_confirmed = False
                    self._image_identity = None
                    self._clear_ownership_claim()
            if cancellation_failure is not None:
                raise cancellation_failure
        finally:
            self._closing_requests -= 1

    async def cancel_active(self) -> bool:
        """协作式取消当前命令；返回是否实际发出了取消。"""

        task = self._active_task
        if task is None or task.done() or task is asyncio.current_task():
            return False
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        return True

    async def execute(
        self,
        command: str | Sequence[str],
        *,
        timeout: float,
        workdir: str = ".",
        on_output: OutputCallback | None = None,
    ) -> ExecutionResult:
        """串行执行命令，并在失败、超时或取消后清理该次容器进程树。

        白名单 fail-fast 校验首个可执行词元，并拒绝隐式复合 shell 控制符；
        shell 只有在 profile 与上层 Tool policy 双重授权后才能使用。
        """

        if timeout <= 0:
            raise ValueError("timeout 必须为正数")
        command_argv = self._command_argv(command)
        resolved_workdir = self._paths.resolve(workdir)
        await self.start()

        async with self._operation_lock:
            container_id = self._require_operation_container()
            current = asyncio.current_task()
            assert current is not None
            self._active_task = current
            result: DockerCommandResult | None = None
            failure: BaseException | None = None
            baseline: dict[str, str] | None = None
            try:
                baseline = await self.capture_process_snapshot()
                result = await self._stream_with_output_watchdog(
                    (
                        "exec",
                        "--workdir",
                        resolved_workdir,
                        container_id,
                        *command_argv,
                    ),
                    timeout=timeout,
                    on_output=on_output,
                )
            except BaseException as exc:
                failure = exc
            finally:
                try:
                    if baseline is not None:
                        await asyncio.shield(
                            self.cleanup_operation_processes(baseline)
                        )
                except BaseException as cleanup_exc:
                    if failure is not None:
                        cleanup_exc.add_note(
                            f"execution also failed with {type(failure).__name__}"
                        )
                    raise
                finally:
                    if self._active_task is current:
                        self._active_task = None
            if failure is not None:
                raise failure
            assert result is not None
            return ExecutionResult(result.returncode, result.stdout, result.stderr)

    async def write_bytes(self, path: str, data: bytes) -> None:
        if len(data) > self.profile.write_max_bytes:
            raise ValueError(
                f"写入 payload 超过 profile write_max_bytes={self.profile.write_max_bytes}"
            )
        target = self._paths.resolve(path)
        await self.start()
        async with self._operation_lock:
            result = await self._file_command("write", target, input_data=data)
            self._ensure_success(result)

    async def read_bytes(self, path: str, *, max_bytes: int | None = None) -> bytes:
        target = self._paths.resolve(path)
        limit = max_bytes if max_bytes is not None else self.profile.read_max_bytes
        if limit <= 0 or limit > self.profile.read_max_bytes:
            raise ValueError("max_bytes 必须为正且不能超过 profile read_max_bytes")
        await self.start()
        async with self._operation_lock:
            result = await self._file_command("read", target, str(limit), stdout_max_bytes=limit)
            self._ensure_success(result)
            return result.stdout

    async def ensure_dir(self, path: str) -> None:
        target = self._paths.resolve(path)
        await self.start()
        async with self._operation_lock:
            result = await self._file_command("mkdir", target)
            self._ensure_success(result)

    async def list_files(self, path: str = ".") -> tuple[str, ...]:
        target = self._paths.resolve(path)
        rows = await self._json_file_query("list", target)
        return tuple(str(row) for row in rows)

    async def glob(self, pattern: str, *, path: str = ".") -> tuple[str, ...]:
        target = self._paths.resolve(path)
        safe_pattern = self._paths.validate_glob(pattern)
        rows = await self._json_file_query("glob", target, safe_pattern)
        return tuple(str(row) for row in rows)

    async def grep(
        self,
        pattern: str,
        *,
        path: str = ".",
        max_matches: int = 100,
    ) -> tuple[GrepMatch, ...]:
        if max_matches <= 0:
            raise ValueError("max_matches 必须为正整数")
        target = self._paths.resolve(path)
        rows = await self._json_file_query("grep", target, pattern, str(max_matches))
        return tuple(GrepMatch(path=str(row[0]), line=int(row[1]), text=str(row[2])) for row in rows)

    def metadata(self) -> Mapping[str, Any]:
        """返回脱敏 runtime metadata；不包含宿主 workspace 绝对路径或 env 值。"""

        container_identity = self._container_id[:12] if self._container_id else None
        return {
            "backend": self.backend_name,
            "profile": self.profile.safe_metadata(),
            "authorization": {
                "network": self.authorization.allow_network,
                "shell": self.authorization.allow_shell,
            },
            "container": {
                "identity": container_identity,
                "ownership": "owned",
                "started": self._started,
            },
            "image_identity": self._image_identity,
            "workspace": {
                "logical_identity": self._workspace_identity,
                "container_root": self.profile.workspace_root,
                "lifecycle": "conversation-owned",
            },
        }

    def _docker_run_args(self, *, name: str, image_identity: str) -> tuple[str, ...]:
        base_mount = (
            f"type=bind,source={self._host_workspace},"
            f"target={self.profile.workspace_root}"
        )
        if self._invocation_output is not None:
            base_mount += ",readonly"
        args = [
            "run",
            "-d",
            "--name",
            name,
            "--workdir",
            self.profile.workspace_root,
            "--network",
            self.profile.network,
            "--memory",
            str(self.profile.memory_bytes),
            "--cpus",
            str(self.profile.cpus),
            "--pids-limit",
            str(self.profile.pids_limit),
            "--init",
            "--user",
            self.profile.user,
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--mount",
            base_mount,
            "--ulimit",
            (
                "fsize="
                f"{self.profile.output_file_max_bytes}:"
                f"{self.profile.output_file_max_bytes}"
            ),
            "--label",
            f"omnicell.runtime.profile={self.profile.name}",
            "--label",
            f"omnicell.runtime.version={self.profile.version}",
        ]
        if self._invocation_id is not None:
            args.extend(
                (
                    "--mount",
                    (
                        f"type=bind,source={self._invocation_output},"
                        f"target={self.profile.workspace_root}/"
                        f".omnicell-invocations/{self._invocation_id}"
                    ),
                    "--label",
                    f"omnicell.runtime.invocation={self._invocation_id}",
                )
            )
        if self.profile.read_only_root:
            args.append("--read-only")
        for tmpfs in self.profile.tmpfs:
            args.extend(("--tmpfs", tmpfs))
        for key in sorted(self.profile.env):
            args.extend(("--env", f"{key}={self.profile.env[key]}"))
        args.extend(
            (
                image_identity,
                "python",
                "-c",
                _CONTAINER_IDLE_SCRIPT,
            )
        )
        return tuple(args)

    def _prepare_invocation_output(self) -> None:
        if self._invocation_id is None or self._invocation_output is None:
            return
        directory_flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        root_fd = os.open(self._host_workspace, directory_flags)
        invocation_root_fd = -1
        invocation_fd = -1
        try:
            try:
                os.mkdir(".omnicell-invocations", mode=0o700, dir_fd=root_fd)
            except FileExistsError:
                pass
            invocation_root_fd = os.open(
                ".omnicell-invocations", directory_flags, dir_fd=root_fd
            )
            try:
                os.mkdir(self._invocation_id, mode=0o700, dir_fd=invocation_root_fd)
            except FileExistsError:
                pass
            invocation_fd = os.open(
                self._invocation_id, directory_flags, dir_fd=invocation_root_fd
            )
            # The host-only parent remains 0700. The mounted leaf is writable by
            # the configured non-root container uid without exposing siblings.
            os.fchmod(invocation_fd, 0o777)
        except OSError as exc:
            raise RuntimeBackendError(
                "invocation output scope 无法安全创建"
            ) from exc
        finally:
            if invocation_fd >= 0:
                os.close(invocation_fd)
            if invocation_root_fd >= 0:
                os.close(invocation_root_fd)
            os.close(root_fd)

    async def _stream_with_output_watchdog(
        self,
        args: tuple[str, ...],
        *,
        timeout: float,
        on_output: OutputCallback | None,
    ) -> DockerCommandResult:
        stream_task = asyncio.create_task(
            self._docker.stream(
                args,
                timeout=timeout,
                stdout_max_bytes=self.profile.stdout_max_bytes,
                stderr_max_bytes=self.profile.stderr_max_bytes,
                on_output=on_output,
                check=False,
            )
        )
        if self._invocation_output is None:
            return await stream_task
        monitor_task = asyncio.create_task(self._monitor_output_quota())
        try:
            done, _ = await asyncio.wait(
                {stream_task, monitor_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if stream_task in done:
                return await stream_task
            await monitor_task
            raise AssertionError("output quota monitor unexpectedly returned")
        finally:
            for task in (stream_task, monitor_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(stream_task, monitor_task, return_exceptions=True)

    async def _monitor_output_quota(self) -> None:
        assert self._invocation_output is not None
        while True:
            try:
                await asyncio.to_thread(
                    scan_output_tree,
                    self._invocation_output,
                    self._output_quota,
                )
            except OutputQuotaViolation as exc:
                raise RuntimeBackendError(
                    "invocation output 超过 runtime 硬上限"
                ) from exc
            await asyncio.sleep(0.05)

    async def _ensure_image(self) -> tuple[dict[str, Any], str]:
        if self.profile.pull_policy is PullPolicy.ALWAYS:
            await self._pull_image()
            return await self._inspect_image(self.profile.image)
        try:
            return await self._inspect_image(self.profile.image)
        except ImageUnavailableError:
            if self.profile.pull_policy is PullPolicy.NEVER:
                raise
            await self._pull_image()
            return await self._inspect_image(self.profile.image)

    async def _pull_image(self) -> None:
        await self._docker.run(
            ("pull", self.profile.image),
            timeout=300,
            stdout_max_bytes=self.profile.stdout_max_bytes,
            stderr_max_bytes=self.profile.stderr_max_bytes,
        )

    async def _inspect_image(self, image: str) -> tuple[dict[str, Any], str]:
        result = await self._docker.run(
            ("image", "inspect", image),
            timeout=30,
            stdout_max_bytes=self.profile.read_max_bytes,
            stderr_max_bytes=self.profile.stderr_max_bytes,
            check=False,
        )
        if result.returncode != 0:
            raise ImageUnavailableError(f"runtime image 不可用：{self.profile.image}")
        try:
            payload = json.loads(result.stdout)[0]
        except (IndexError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeBackendError("无法解析 docker image inspect 输出") from exc
        digests = payload.get("RepoDigests") or []
        identity = str(digests[0] if digests else payload.get("Id") or "")
        if not identity:
            raise RuntimeBackendError("镜像缺少 RepoDigest 和 Id")
        return payload, identity

    async def capture_process_snapshot(self) -> dict[str, str]:
        """记录当前容器进程身份，用于清理本次 operation 新产生的进程。"""

        container_id = self._require_container()
        result = await self._docker.run(
            ("exec", container_id, "python", "-c", _PROCESS_SNAPSHOT_SCRIPT),
            timeout=10,
            stdout_max_bytes=64 * 1024,
            stderr_max_bytes=4096,
        )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeBackendError("无法解析容器进程 snapshot") from exc
        if not isinstance(payload, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in payload.items()
        ):
            raise RuntimeBackendError("容器进程 snapshot 结构非法")
        return payload

    async def cleanup_processes(self, baseline: Mapping[str, str]) -> None:
        """清理并复验 baseline 之后出现的全部容器进程。"""

        container_id = self._require_container()
        encoded = json.dumps(dict(baseline), sort_keys=True, separators=(",", ":"))
        result = await self._docker.run(
            ("exec", container_id, "python", "-c", _PROCESS_CLEANUP_SCRIPT, encoded),
            timeout=15,
            stdout_max_bytes=4096,
            stderr_max_bytes=4096,
            check=False,
        )
        self._ensure_success(result)

    async def cleanup_operation_processes(self, baseline: Mapping[str, str]) -> None:
        """清理一次 operation，并在无法证明干净时令容器失效。"""

        container_id = self._require_container()
        try:
            await self.cleanup_processes(baseline)
        except BaseException as cleanup_exc:
            try:
                await asyncio.shield(
                    self._invalidate_after_cleanup_failure(container_id)
                )
            except BaseException as discard_exc:
                cleanup_exc.add_note(
                    "unsafe container discard also failed with "
                    f"{type(discard_exc).__name__}"
                )
            raise

    async def _remove_owned_container(
        self,
        identifier: str,
        *,
        missing_is_safe: bool = True,
    ) -> None:
        removal_identifier = identifier
        if not missing_is_safe:
            inspected = await self._docker.run(
                ("container", "inspect", identifier),
                timeout=30,
                stdout_max_bytes=64 * 1024,
                stderr_max_bytes=self.profile.stderr_max_bytes,
                check=False,
            )
            if inspected.returncode != 0:
                missing = inspected.stderr.lower()
                if b"no such container" in missing or b"no such object" in missing:
                    raise RuntimeBackendError(
                        "provisional Docker container 回收尚未确认"
                    )
                raise DockerCommandError(
                    inspected.args,
                    inspected.returncode,
                    inspected.stderr,
                )
            try:
                payload = json.loads(inspected.stdout)
                record = payload[0]
                removal_identifier = str(record["Id"])
                labels = record["Config"]["Labels"] or {}
            except (IndexError, KeyError, TypeError, json.JSONDecodeError) as exc:
                raise RuntimeBackendError(
                    "provisional Docker ownership inspect 响应非法"
                ) from exc
            if not _IMMUTABLE_CONTAINER_ID_PATTERN.fullmatch(removal_identifier):
                raise RuntimeBackendError(
                    "provisional Docker immutable identity 非法"
                )
            if (
                self._invocation_id is not None
                and labels.get("omnicell.runtime.invocation")
                != self._invocation_id
            ):
                raise RuntimeBackendError(
                    "provisional Docker ownership label 不匹配"
                )
        result = await self._docker.run(
            ("rm", "-f", removal_identifier),
            timeout=30,
            stdout_max_bytes=4096,
            stderr_max_bytes=self.profile.stderr_max_bytes,
            check=False,
        )
        if result.returncode == 0:
            return
        missing = result.stderr.lower()
        if b"no such container" in missing or b"no such object" in missing:
            if missing_is_safe:
                return
            raise RuntimeBackendError(
                "provisional Docker container 回收尚未确认"
            )
        raise DockerCommandError(result.args, result.returncode, result.stderr)

    def _write_ownership_claim(self, container_id: str, *, state: str) -> None:
        if self._ownership_file is None or self._invocation_id is None:
            return
        if state not in {"provisional", "confirmed"}:
            raise RuntimeBackendError("runtime ownership claim state 非法")
        payload = json.dumps(
            {
                "invocation_id": self._invocation_id,
                "container_id": container_id,
                "state": state,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self._ownership_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._ownership_file.with_name(
            f".{self._ownership_file.name}.{os.getpid()}.tmp"
        )
        try:
            with temporary.open("wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._ownership_file)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def _clear_ownership_claim(self) -> None:
        if self._ownership_file is None:
            return
        try:
            self._ownership_file.unlink()
        except FileNotFoundError:
            pass

    async def _invalidate_after_cleanup_failure(self, container_id: str) -> None:
        """将无法证明干净的容器置为不可用，并尽力立即销毁。"""

        async with self._state_lock:
            if self._container_id != container_id:
                return
            self._started = False
            await self._remove_owned_container(
                container_id,
                missing_is_safe=self._container_identity_confirmed,
            )
            self._container_id = None
            self._container_identity_confirmed = False
            self._image_identity = None
            self._clear_ownership_claim()

    async def _file_command(
        self,
        operation: str,
        target: str,
        *extra: str,
        input_data: bytes | None = None,
        stdout_max_bytes: int | None = None,
    ) -> DockerCommandResult:
        control_size = sum(
            len(value.encode("utf-8")) + 1
            for value in (operation, target, *extra)
        )
        if control_size > self.profile.command_max_bytes:
            raise CommandNotAllowedError(
                "文件操作参数超过 profile "
                f"command_max_bytes={self.profile.command_max_bytes}"
            )
        container_id = self._require_operation_container()
        baseline = await self.capture_process_snapshot()
        result: DockerCommandResult | None = None
        failure: BaseException | None = None
        try:
            result = await self._docker.run(
                (
                    "exec",
                    *(("-i",) if input_data is not None else ()),
                    container_id,
                    "python",
                    "-c",
                    _FILE_SCRIPT,
                    operation,
                    self.profile.workspace_root,
                    target,
                    *extra,
                ),
                timeout=60,
                stdout_max_bytes=stdout_max_bytes or self.profile.read_max_bytes,
                stderr_max_bytes=self.profile.stderr_max_bytes,
                input_data=input_data,
                check=False,
            )
        except BaseException as exc:
            failure = exc
        finally:
            try:
                await asyncio.shield(self.cleanup_operation_processes(baseline))
            except BaseException as cleanup_exc:
                if failure is not None:
                    cleanup_exc.add_note(
                        f"file operation also failed with {type(failure).__name__}"
                    )
                raise
        if failure is not None:
            raise failure
        assert result is not None
        return result

    async def _json_file_query(self, operation: str, target: str, *extra: str) -> list[Any]:
        await self.start()
        async with self._operation_lock:
            result = await self._file_command(operation, target, *extra)
            self._ensure_success(result)
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeBackendError(f"{operation} 返回了非法 JSON") from exc
        if not isinstance(payload, list):
            raise RuntimeBackendError(f"{operation} 返回值必须是 list")
        return payload

    @staticmethod
    def _ensure_success(result: DockerCommandResult) -> None:
        if result.returncode != 0:
            raise DockerCommandError(result.args, result.returncode, result.stderr)

    def _require_container(self) -> str:
        if not self._started or not self._container_id:
            raise RuntimeNotStartedError("Local Docker Backend 尚未启动")
        return self._container_id

    def _require_operation_container(self) -> str:
        if self._closing_requests:
            raise RuntimeBackendError("Local Docker Backend 正在关闭，拒绝新的 operation")
        return self._require_container()

    def _command_argv(self, command: str | Sequence[str]) -> tuple[str, ...]:
        if isinstance(command, str):
            try:
                tokens = shlex.split(command, posix=True)
            except ValueError as exc:
                raise CommandNotAllowedError("命令参数语法不完整") from exc
        else:
            tokens = [str(token) for token in command]
        if not tokens:
            raise CommandNotAllowedError("命令不能为空")
        if any("\x00" in token for token in tokens):
            raise CommandNotAllowedError("命令参数不得包含 NUL 字符")
        command_size = sum(len(token.encode("utf-8")) + 1 for token in tokens)
        if command_size > self.profile.command_max_bytes:
            raise CommandNotAllowedError(
                f"命令输入超过 profile command_max_bytes={self.profile.command_max_bytes}"
            )
        executable = tokens[0]
        if "/" in executable or executable not in self.profile.allowed_commands:
            raise CommandNotAllowedError(f"命令不在 profile 白名单中：{executable}")
        if executable in self.profile.shell_commands and not self.authorization.allow_shell:
            raise CommandNotAllowedError("shell 命令需要 Tool policy 显式授权")
        if executable not in self.profile.shell_commands and isinstance(command, str):
            lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
            lexer.whitespace_split = True
            lexer.commenters = ""
            shell_controls = {";", "&", "&&", "|", "||", "<", ">", ">>", "<<", "(", ")"}
            if "\n" in command or "\r" in command or "`" in command or any(
                token in shell_controls for token in lexer
            ):
                raise CommandNotAllowedError(
                    "复合 shell 语法需要显式使用 profile 允许的 shell"
                )
        return tuple(tokens)
