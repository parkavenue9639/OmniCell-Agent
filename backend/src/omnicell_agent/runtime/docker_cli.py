"""有界、可取消的异步 Docker CLI adapter。"""

from __future__ import annotations

import asyncio
import inspect
import os
import signal
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass

from .errors import DockerCommandError, DockerCommandTimeout, OutputLimitExceeded


@dataclass(frozen=True, slots=True)
class OutputDelta:
    """一次 stdout/stderr 增量。"""

    stream: str
    data: bytes


@dataclass(frozen=True, slots=True)
class DockerCommandResult:
    """一次 Docker CLI 调用结果。"""

    args: tuple[str, ...]
    returncode: int
    stdout: bytes
    stderr: bytes


OutputCallback = Callable[[OutputDelta], Awaitable[None] | None]


class DockerCLI:
    """仅通过 ``create_subprocess_exec`` 调用 Docker，不经过 shell。"""

    def __init__(
        self,
        executable: str = "docker",
        *,
        env: Mapping[str, str] | None = None,
    ):
        self.executable = executable
        self.env = dict(env) if env is not None else None

    async def run(
        self,
        args: Sequence[str],
        *,
        timeout: float | None = None,
        stdout_max_bytes: int = 1024 * 1024,
        stderr_max_bytes: int = 1024 * 1024,
        input_data: bytes | None = None,
        check: bool = True,
    ) -> DockerCommandResult:
        return await self._execute(
            args,
            timeout=timeout,
            stdout_max_bytes=stdout_max_bytes,
            stderr_max_bytes=stderr_max_bytes,
            input_data=input_data,
            check=check,
            on_output=None,
        )

    async def stream(
        self,
        args: Sequence[str],
        *,
        timeout: float | None,
        stdout_max_bytes: int,
        stderr_max_bytes: int,
        on_output: OutputCallback | None,
        check: bool = True,
    ) -> DockerCommandResult:
        return await self._execute(
            args,
            timeout=timeout,
            stdout_max_bytes=stdout_max_bytes,
            stderr_max_bytes=stderr_max_bytes,
            input_data=None,
            check=check,
            on_output=on_output,
        )

    async def _execute(
        self,
        args: Sequence[str],
        *,
        timeout: float | None,
        stdout_max_bytes: int,
        stderr_max_bytes: int,
        input_data: bytes | None,
        check: bool,
        on_output: OutputCallback | None,
    ) -> DockerCommandResult:
        if stdout_max_bytes <= 0 or stderr_max_bytes <= 0:
            raise ValueError("输出上限必须为正整数")
        argv = tuple(str(arg) for arg in args)
        process = await asyncio.create_subprocess_exec(
            self.executable,
            *argv,
            stdin=asyncio.subprocess.PIPE if input_data is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self.env,
            start_new_session=True,
        )
        assert process.stdout is not None
        assert process.stderr is not None

        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []

        async def read_bounded(
            reader: asyncio.StreamReader,
            stream: str,
            limit: int,
            chunks: list[bytes],
        ) -> None:
            observed = 0
            while chunk := await reader.read(64 * 1024):
                observed += len(chunk)
                if observed > limit:
                    raise OutputLimitExceeded(stream, limit)
                chunks.append(chunk)
                if on_output is not None:
                    outcome = on_output(OutputDelta(stream=stream, data=chunk))
                    if inspect.isawaitable(outcome):
                        await outcome

        async def write_input() -> None:
            if input_data is None:
                return
            assert process.stdin is not None
            process.stdin.write(input_data)
            await process.stdin.drain()
            process.stdin.close()
            await process.stdin.wait_closed()

        tasks = [
            asyncio.create_task(read_bounded(process.stdout, "stdout", stdout_max_bytes, stdout_chunks)),
            asyncio.create_task(read_bounded(process.stderr, "stderr", stderr_max_bytes, stderr_chunks)),
            asyncio.create_task(write_input()),
            asyncio.create_task(process.wait()),
        ]
        try:
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=timeout)
        except asyncio.TimeoutError as exc:
            await self._terminate(process, tasks)
            raise DockerCommandTimeout(f"Docker CLI 超过 {timeout} 秒 wall timeout") from exc
        except asyncio.CancelledError:
            await self._terminate(process, tasks)
            raise
        except BaseException:
            await self._terminate(process, tasks)
            raise

        result = DockerCommandResult(
            args=argv,
            returncode=process.returncode or 0,
            stdout=b"".join(stdout_chunks),
            stderr=b"".join(stderr_chunks),
        )
        if check and result.returncode != 0:
            raise DockerCommandError(argv, result.returncode, result.stderr)
        return result

    @staticmethod
    async def _terminate(
        process: asyncio.subprocess.Process,
        tasks: Sequence[asyncio.Task[object]],
    ) -> None:
        for task in tasks:
            if not task.done():
                task.cancel()
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                process.kill()
        await asyncio.gather(*tasks, return_exceptions=True)
        if process.returncode is None:
            await process.wait()
