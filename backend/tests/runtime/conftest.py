from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from omnicell_agent.runtime import (
    DockerCommandResult,
    OutputDelta,
    RuntimeProfile,
)


class FakeDockerCLI:
    """不连接 daemon 的可编程 Docker CLI 替身。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...], dict[str, Any]]] = []
        self.image_payload: dict[str, Any] = {
            "Id": "sha256:image-id",
            "RepoDigests": ["example/worker@sha256:digest"],
        }
        self.image_available = True
        self.container_payload: dict[str, Any] = {
            "Id": "a" * 64,
            "Image": "sha256:attached-image",
            "State": {"Running": True},
            "Config": {"Labels": {}},
        }
        self.container_counter = 0
        self.run_error_after_create: BaseException | None = None
        self.rm_returncode = 0
        self.rm_missing = False
        self.cleanup_returncode = 0
        self.process_snapshot: dict[str, str] = {}
        self.stream_result = DockerCommandResult(("exec",), 0, b"stdout", b"stderr")
        self.stream_error: BaseException | None = None
        self.stream_started = asyncio.Event()
        self.block_stream = False
        self.stream_cancelled = False
        self.file_responses: dict[str, bytes] = {}

    async def run(self, args: Any, **kwargs: Any) -> DockerCommandResult:
        argv = tuple(args)
        self.calls.append(("run", argv, dict(kwargs)))
        if argv[:2] == ("image", "inspect"):
            if not self.image_available:
                return DockerCommandResult(argv, 1, b"", b"missing")
            return DockerCommandResult(argv, 0, json.dumps([self.image_payload]).encode(), b"")
        if argv and argv[0] == "pull":
            self.image_available = True
            return DockerCommandResult(argv, 0, b"pulled", b"")
        if argv[:2] == ("container", "inspect"):
            if self.rm_missing:
                return DockerCommandResult(argv, 1, b"", b"No such container")
            return DockerCommandResult(argv, 0, json.dumps([self.container_payload]).encode(), b"")
        if argv and argv[0] == "inspect":
            return DockerCommandResult(argv, 0, json.dumps([self.container_payload]).encode(), b"")
        if argv[:2] == ("run", "-d"):
            self.container_counter += 1
            if self.run_error_after_create is not None:
                raise self.run_error_after_create
            return DockerCommandResult(argv, 0, f"container-{self.container_counter}".encode(), b"")
        if argv[:2] == ("rm", "-f"):
            if self.rm_missing:
                return DockerCommandResult(argv, 1, b"", b"No such container")
            stderr = b"remove failed" if self.rm_returncode else b""
            return DockerCommandResult(argv, self.rm_returncode, b"", stderr)
        if argv and argv[0] == "exec" and "python" in argv:
            script_index = argv.index("python")
            script = argv[script_index + 2]
            if "snapshot = {}" in script:
                return DockerCommandResult(
                    argv, 0, json.dumps(self.process_snapshot).encode(), b""
                )
            if "remaining_pids" in script:
                stderr = b'{"remaining_pids":[42]}' if self.cleanup_returncode else b""
                return DockerCommandResult(argv, self.cleanup_returncode, b"", stderr)
            operation = argv[script_index + 3]
            stdout = self.file_responses.get(operation, b"")
            return DockerCommandResult(argv, 0, stdout, b"")
        return DockerCommandResult(argv, 0, b"", b"")

    async def stream(self, args: Any, **kwargs: Any) -> DockerCommandResult:
        argv = tuple(args)
        self.calls.append(("stream", argv, dict(kwargs)))
        self.stream_started.set()
        if self.block_stream:
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                self.stream_cancelled = True
                raise
        if self.stream_error is not None:
            raise self.stream_error
        callback = kwargs.get("on_output")
        if callback is not None:
            outcome = callback(OutputDelta("stdout", self.stream_result.stdout))
            if asyncio.iscoroutine(outcome):
                await outcome
        return DockerCommandResult(argv, self.stream_result.returncode, self.stream_result.stdout, self.stream_result.stderr)


@pytest.fixture
def profile() -> RuntimeProfile:
    return RuntimeProfile(
        name="science-v1",
        image="example/worker:latest",
        env={"OMP_NUM_THREADS": "2"},
        allowed_commands=("python", "sh"),
        shell_commands=("sh",),
        stdout_max_bytes=1024,
        stderr_max_bytes=2048,
        read_max_bytes=4096,
    )


@pytest.fixture
def fake_docker() -> FakeDockerCLI:
    return FakeDockerCLI()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path / "conversation-001"
