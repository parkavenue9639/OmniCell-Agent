from __future__ import annotations

import asyncio
import sys

import pytest

from omnicell_agent.runtime import DockerCLI, DockerCommandTimeout, OutputDelta, OutputLimitExceeded
from omnicell_agent.runtime.errors import DockerCommandError


@pytest.mark.asyncio
async def test_docker_cli_streams_bounded_deltas_without_shell() -> None:
    cli = DockerCLI(executable=sys.executable)
    deltas: list[OutputDelta] = []

    result = await cli.stream(
        ("-c", "import sys; print('out'); print('err', file=sys.stderr)"),
        timeout=5,
        stdout_max_bytes=128,
        stderr_max_bytes=128,
        on_output=deltas.append,
    )

    assert result.returncode == 0
    assert result.stdout == b"out\n"
    assert result.stderr == b"err\n"
    assert {delta.stream for delta in deltas} == {"stdout", "stderr"}


@pytest.mark.asyncio
async def test_docker_cli_output_limit_kills_and_reaps_process() -> None:
    cli = DockerCLI(executable=sys.executable)

    with pytest.raises(OutputLimitExceeded, match="stdout"):
        await cli.run(
            ("-c", "import sys,time; sys.stdout.write('x'*10000); sys.stdout.flush(); time.sleep(30)"),
            timeout=5,
            stdout_max_bytes=64,
            stderr_max_bytes=64,
        )


@pytest.mark.asyncio
async def test_docker_cli_timeout_kills_and_reaps_process() -> None:
    cli = DockerCLI(executable=sys.executable)

    with pytest.raises(DockerCommandTimeout):
        await cli.run(
            ("-c", "import time; time.sleep(30)"),
            timeout=0.05,
            stdout_max_bytes=64,
            stderr_max_bytes=64,
        )


@pytest.mark.asyncio
async def test_docker_cli_cancellation_kills_and_reaps_process() -> None:
    cli = DockerCLI(executable=sys.executable)
    task = asyncio.create_task(
        cli.run(
            ("-c", "import time; time.sleep(30)"),
            timeout=10,
            stdout_max_bytes=64,
            stderr_max_bytes=64,
        )
    )
    await asyncio.sleep(0.05)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2)


def test_docker_command_error_redacts_env_and_host_mount() -> None:
    args = (
        "run",
        "--env",
        "SAFE_ENV=private-value",
        "--mount",
        "type=bind,source=/Users/name/private,target=/app/data",
    )
    error = DockerCommandError(args, 1, b"private-value /Users/name/private")

    rendered = repr(error.args_tuple) + str(error) + error.stderr.decode()
    assert "private-value" not in rendered
    assert "/Users/name/private" not in rendered
    assert "SAFE_ENV=<redacted>" in rendered
