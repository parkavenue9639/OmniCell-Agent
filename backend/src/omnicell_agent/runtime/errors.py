"""Local Docker Backend 的类型化错误。"""

from __future__ import annotations


class RuntimeBackendError(RuntimeError):
    """执行后端的基础错误。"""


class InvalidRuntimeProfileError(RuntimeBackendError, ValueError):
    """Runtime profile 不满足安全约束。"""


class RuntimePathError(RuntimeBackendError, ValueError):
    """Agent 路径越过 conversation workspace。"""


class DockerCLIError(RuntimeBackendError):
    """Docker CLI 调用失败。"""


class DockerCommandError(DockerCLIError):
    """Docker CLI 返回非零状态。"""

    def __init__(self, args: tuple[str, ...], returncode: int, stderr: bytes):
        self.args_tuple = _sanitize_docker_args(args)
        self.returncode = returncode
        self.stderr = _sanitize_docker_stderr(args, stderr)
        detail = self.stderr.decode("utf-8", errors="replace").strip()
        super().__init__(
            f"Docker CLI 执行失败（exit={returncode}, command={args[0] if args else '<empty>'}）"
            + (f"：{detail}" if detail else "")
        )


def _sanitize_docker_args(args: tuple[str, ...]) -> tuple[str, ...]:
    """隐藏容器 env 值与 bind mount 的宿主 source。"""

    sanitized: list[str] = []
    redact_next_env = False
    redact_next_mount = False
    for arg in args:
        if redact_next_env:
            key = arg.split("=", 1)[0]
            sanitized.append(f"{key}=<redacted>")
            redact_next_env = False
            continue
        if redact_next_mount:
            parts = []
            for part in arg.split(","):
                if part.startswith(("source=", "src=")):
                    parts.append(part.split("=", 1)[0] + "=<redacted>")
                else:
                    parts.append(part)
            sanitized.append(",".join(parts))
            redact_next_mount = False
            continue
        sanitized.append(arg)
        if arg in {"--env", "-e"}:
            redact_next_env = True
        elif arg == "--mount":
            redact_next_mount = True
    return tuple(sanitized)


def _sanitize_docker_stderr(args: tuple[str, ...], stderr: bytes) -> bytes:
    """从错误文本中移除已知 env 值和宿主 bind source。"""

    text = stderr.decode("utf-8", errors="replace")
    for index, arg in enumerate(args[:-1]):
        value = args[index + 1]
        if arg in {"--env", "-e"} and "=" in value:
            secret = value.split("=", 1)[1]
            if secret:
                text = text.replace(secret, "<redacted>")
        elif arg == "--mount":
            for part in value.split(","):
                if part.startswith(("source=", "src=")):
                    source = part.split("=", 1)[1]
                    if source:
                        text = text.replace(source, "<redacted>")
    return text.encode("utf-8")


class DockerCommandTimeout(DockerCLIError, TimeoutError):
    """Docker CLI 或容器执行超过 wall timeout。"""


class OutputLimitExceeded(DockerCLIError):
    """stdout 或 stderr 超过硬上限。"""

    def __init__(self, stream: str, limit: int):
        self.stream = stream
        self.limit = limit
        super().__init__(f"{stream} 超过允许的 {limit} bytes 硬上限")


class ImageUnavailableError(RuntimeBackendError):
    """所需镜像不可用且 pull policy 不允许恢复。"""


class CommandNotAllowedError(RuntimeBackendError, PermissionError):
    """命令首个可执行词元不在 profile 白名单中。"""


class RuntimeNotStartedError(RuntimeBackendError):
    """需要容器已启动但当前没有可用容器。"""
