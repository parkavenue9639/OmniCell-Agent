"""不可变、可版本化的 runtime profile。"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Any

from .errors import InvalidRuntimeProfileError
from .output_policy import (
    DEFAULT_OUTPUT_FILE_MAX_BYTES,
    DEFAULT_OUTPUT_MAX_FILES,
    DEFAULT_OUTPUT_TOTAL_MAX_BYTES,
)


_PROFILE_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")
_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SECRET_KEY = re.compile(
    r"(^|_)(?:access_?key(?:_id)?|api_?key|auth|authorization|bearer|client_?secret|cookies?|credentials?|database_?url|dsn|passwd|password|private_?key|secret|session|ssh_?key|token)(?:_|$)",
    re.IGNORECASE,
)
_KNOWN_SHELL_BASENAMES = frozenset(
    {
        "ash",
        "bash",
        "csh",
        "dash",
        "elvish",
        "fish",
        "ksh",
        "mksh",
        "nu",
        "pwsh",
        "sh",
        "tcsh",
        "xonsh",
        "zsh",
    }
)


class PullPolicy(StrEnum):
    """镜像获取策略。"""

    NEVER = "never"
    IF_NOT_PRESENT = "if_not_present"
    ALWAYS = "always"


@dataclass(frozen=True, slots=True)
class RuntimeAuthorization:
    """由上层 Tool policy 显式授予的高风险 runtime 能力。"""

    allow_network: bool = False
    allow_shell: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.allow_network, bool) or not isinstance(self.allow_shell, bool):
            raise InvalidRuntimeProfileError("runtime authorization 只能使用布尔授权值")


def _positive_int(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise InvalidRuntimeProfileError(f"{name} 必须是正整数")


def _positive_number(name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise InvalidRuntimeProfileError(f"{name} 必须是正数")


@dataclass(frozen=True, slots=True)
class RuntimeProfile:
    """容器运行约束；实例一经构造即不可变。"""

    name: str
    image: str
    version: int = 1
    workspace_root: str = "/app/data"
    network: str = "none"
    memory_bytes: int = 4 * 1024 * 1024 * 1024
    cpus: float = 2.0
    pids_limit: int = 256
    user: str = "1000:1000"
    read_only_root: bool = True
    tmpfs: tuple[str, ...] | Sequence[str] = ("/tmp:rw,noexec,nosuid,size=256m",)
    env: Mapping[str, str] = field(default_factory=dict)
    pull_policy: PullPolicy | str = PullPolicy.IF_NOT_PRESENT
    allowed_commands: tuple[str, ...] | Sequence[str] = ("python", "python3")
    shell_commands: tuple[str, ...] | Sequence[str] = ()
    stdout_max_bytes: int = 1024 * 1024
    stderr_max_bytes: int = 1024 * 1024
    command_max_bytes: int = 1024 * 1024
    write_max_bytes: int = 16 * 1024 * 1024
    read_max_bytes: int = 16 * 1024 * 1024
    output_max_files: int = DEFAULT_OUTPUT_MAX_FILES
    output_file_max_bytes: int = DEFAULT_OUTPUT_FILE_MAX_BYTES
    output_total_max_bytes: int = DEFAULT_OUTPUT_TOTAL_MAX_BYTES

    def __post_init__(self) -> None:
        if not _PROFILE_NAME.fullmatch(self.name):
            raise InvalidRuntimeProfileError("profile name 只能包含字母、数字、点、下划线和短横线")
        if (
            not self.image
            or self.image.strip() != self.image
            or self.image.startswith("-")
            or "\x00" in self.image
        ):
            raise InvalidRuntimeProfileError("image 不能为空或包含首尾空白")
        _positive_int("version", self.version)

        if "\x00" in self.workspace_root:
            raise InvalidRuntimeProfileError("workspace_root 不得包含 NUL 字符")
        root = PurePosixPath(self.workspace_root)
        if not root.is_absolute() or root == PurePosixPath("/") or ".." in root.parts:
            raise InvalidRuntimeProfileError("workspace_root 必须是非根目录的绝对 POSIX 路径")
        normalized_root = str(root)
        object.__setattr__(self, "workspace_root", normalized_root)

        if self.network not in {"none", "bridge"}:
            raise InvalidRuntimeProfileError("network 只允许 none 或 bridge")
        _positive_int("memory_bytes", self.memory_bytes)
        _positive_number("cpus", self.cpus)
        _positive_int("pids_limit", self.pids_limit)
        _positive_int("stdout_max_bytes", self.stdout_max_bytes)
        _positive_int("stderr_max_bytes", self.stderr_max_bytes)
        _positive_int("command_max_bytes", self.command_max_bytes)
        _positive_int("write_max_bytes", self.write_max_bytes)
        _positive_int("read_max_bytes", self.read_max_bytes)
        _positive_int("output_max_files", self.output_max_files)
        _positive_int("output_file_max_bytes", self.output_file_max_bytes)
        _positive_int("output_total_max_bytes", self.output_total_max_bytes)
        if self.output_file_max_bytes > self.output_total_max_bytes:
            raise InvalidRuntimeProfileError(
                "output_file_max_bytes 不能超过 output_total_max_bytes"
            )

        if not self.user or self.user.strip() != self.user:
            raise InvalidRuntimeProfileError("user 不能为空或包含首尾空白")
        if "\x00" in self.user:
            raise InvalidRuntimeProfileError("user 不得包含 NUL 字符")

        normalized_tmpfs = tuple(self.tmpfs)
        for entry in normalized_tmpfs:
            if not isinstance(entry, str) or "\x00" in entry:
                raise InvalidRuntimeProfileError("tmpfs 条目必须是不含 NUL 的字符串")
            path = entry.split(":", 1)[0]
            if not path.startswith("/") or path == "/":
                raise InvalidRuntimeProfileError("tmpfs 必须挂载到非根绝对路径")
            tmpfs_path = PurePosixPath(path)
            if (
                tmpfs_path == root
                or tmpfs_path in root.parents
                or root in tmpfs_path.parents
            ):
                raise InvalidRuntimeProfileError("tmpfs 不得覆盖 conversation workspace")
        object.__setattr__(self, "tmpfs", normalized_tmpfs)

        safe_env: dict[str, str] = {}
        for key, value in self.env.items():
            if not _ENV_KEY.fullmatch(key):
                raise InvalidRuntimeProfileError(f"非法环境变量名：{key}")
            if _SECRET_KEY.search(key):
                raise InvalidRuntimeProfileError(f"profile 禁止声明疑似 secret 的环境变量：{key}")
            if not isinstance(value, str) or "\x00" in value:
                raise InvalidRuntimeProfileError(f"环境变量 {key} 的值必须是字符串")
            safe_env[key] = value
        object.__setattr__(self, "env", MappingProxyType(safe_env))

        try:
            policy = PullPolicy(self.pull_policy)
        except ValueError as exc:
            raise InvalidRuntimeProfileError("pull_policy 非法") from exc
        object.__setattr__(self, "pull_policy", policy)

        commands = tuple(self.allowed_commands)
        if not commands or any(
            not command
            or command.strip() != command
            or "/" in command
            or "\x00" in command
            for command in commands
        ):
            raise InvalidRuntimeProfileError("allowed_commands 必须是非空的可执行文件 basename 列表")
        object.__setattr__(self, "allowed_commands", commands)

        shell_commands = tuple(self.shell_commands)
        if any(
            not command
            or command.strip() != command
            or "/" in command
            or "\x00" in command
            for command in shell_commands
        ):
            raise InvalidRuntimeProfileError(
                "shell_commands 必须是可执行文件 basename 列表"
            )
        unknown_shells = set(shell_commands).difference(commands)
        if unknown_shells:
            raise InvalidRuntimeProfileError(
                "shell_commands 必须同时出现在 allowed_commands"
            )
        ambiguous_shells = _KNOWN_SHELL_BASENAMES.intersection(commands).difference(
            shell_commands
        )
        if ambiguous_shells:
            raise InvalidRuntimeProfileError(
                "已知 shell 必须在 shell_commands 中显式分类"
            )
        object.__setattr__(self, "shell_commands", shell_commands)

    def safe_metadata(self) -> Mapping[str, Any]:
        """返回不包含环境变量值的诊断信息。"""

        return {
            "name": self.name,
            "version": self.version,
            "image": self.image,
            "workspace_root": self.workspace_root,
            "network": self.network,
            "resources": {
                "memory_bytes": self.memory_bytes,
                "cpus": self.cpus,
                "pids_limit": self.pids_limit,
                "user": self.user,
                "read_only_root": self.read_only_root,
                "tmpfs": self.tmpfs,
            },
            "policy": {
                "pull": self.pull_policy.value,
                "allowed_commands": self.allowed_commands,
                "shell_commands": self.shell_commands,
                "command_guard": "直接 argv 与可执行词元白名单；shell 需要 profile 和 Tool policy 双重授权",
                "stdout_max_bytes": self.stdout_max_bytes,
                "stderr_max_bytes": self.stderr_max_bytes,
                "command_max_bytes": self.command_max_bytes,
                "write_max_bytes": self.write_max_bytes,
                "read_max_bytes": self.read_max_bytes,
                "output_max_files": self.output_max_files,
                "output_file_max_bytes": self.output_file_max_bytes,
                "output_total_max_bytes": self.output_total_max_bytes,
            },
            "env_keys": tuple(sorted(self.env)),
        }
