"""OmniCell-Agent 隔离执行后端公共边界。"""

from .cancellation import (
    RuntimeCancellationRegistry,
    register_runtime_cancel,
    runtime_cancellation_scope,
)
from .docker_cli import DockerCLI, DockerCommandResult, OutputCallback, OutputDelta
from .errors import (
    CommandNotAllowedError,
    DockerCLIError,
    DockerCommandError,
    DockerCommandTimeout,
    ImageUnavailableError,
    InvalidRuntimeProfileError,
    OutputLimitExceeded,
    RuntimeBackendError,
    RuntimeNotStartedError,
    RuntimePathError,
)
from .local_docker import ExecutionResult, GrepMatch, LocalDockerBackend
from .paths import WorkspacePathResolver
from .profile import PullPolicy, RuntimeAuthorization, RuntimeProfile
from .python_session import LocalDockerPythonSession

__all__ = [
    "CommandNotAllowedError",
    "DockerCLI",
    "DockerCLIError",
    "DockerCommandError",
    "DockerCommandResult",
    "DockerCommandTimeout",
    "ExecutionResult",
    "GrepMatch",
    "ImageUnavailableError",
    "InvalidRuntimeProfileError",
    "LocalDockerBackend",
    "LocalDockerPythonSession",
    "OutputCallback",
    "OutputDelta",
    "OutputLimitExceeded",
    "PullPolicy",
    "RuntimeCancellationRegistry",
    "RuntimeBackendError",
    "RuntimeAuthorization",
    "RuntimeNotStartedError",
    "RuntimePathError",
    "RuntimeProfile",
    "WorkspacePathResolver",
    "register_runtime_cancel",
    "runtime_cancellation_scope",
]
