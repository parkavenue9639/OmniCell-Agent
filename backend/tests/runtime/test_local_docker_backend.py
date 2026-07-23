from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from omnicell_agent.runtime import (
    CommandNotAllowedError,
    DockerCommandResult,
    DockerCommandError,
    DockerCommandTimeout,
    LocalDockerBackend,
    OutputDelta,
    OutputLimitExceeded,
    PullPolicy,
    RuntimePathError,
    RuntimeAuthorization,
    RuntimeBackendError,
    RuntimeProfile,
)

from .conftest import FakeDockerCLI


def _calls(fake: FakeDockerCLI, prefix: tuple[str, ...]) -> list[tuple[str, ...]]:
    return [args for _, args, _ in fake.calls if args[: len(prefix)] == prefix]


@pytest.mark.asyncio
async def test_lazy_idempotent_start_uses_digest_and_hardened_run_args(
    profile: RuntimeProfile,
    fake_docker: FakeDockerCLI,
    workspace: Path,
) -> None:
    backend = LocalDockerBackend(profile, workspace, docker=fake_docker)
    assert not workspace.exists()
    assert fake_docker.calls == []

    await backend.start()
    await backend.start()

    assert workspace.is_dir()
    run_calls = _calls(fake_docker, ("run", "-d"))
    assert len(run_calls) == 1
    args = run_calls[0]
    assert "example/worker@sha256:digest" in args
    assert args[args.index("--network") + 1] == "none"
    assert args[args.index("--memory") + 1] == str(profile.memory_bytes)
    assert args[args.index("--pids-limit") + 1] == str(profile.pids_limit)
    assert "--init" in args
    assert "--read-only" in args
    assert ("--cap-drop", "ALL") == args[args.index("--cap-drop") : args.index("--cap-drop") + 2]
    assert ("--security-opt", "no-new-privileges") == args[
        args.index("--security-opt") : args.index("--security-opt") + 2
    ]
    mount = args[args.index("--mount") + 1]
    assert str(workspace) in mount
    assert "target=/app/data" in mount
    assert args.count("--mount") == 1
    assert args[-4] == "example/worker@sha256:digest"
    assert "OMP_NUM_THREADS=2" in args


@pytest.mark.asyncio
async def test_missing_digest_falls_back_to_image_id(
    profile: RuntimeProfile,
    fake_docker: FakeDockerCLI,
    workspace: Path,
) -> None:
    fake_docker.image_payload = {"Id": "sha256:immutable-id", "RepoDigests": []}
    backend = LocalDockerBackend(profile, workspace, docker=fake_docker)

    await backend.start()

    assert backend.image_identity == "sha256:immutable-id"
    assert "sha256:immutable-id" in _calls(fake_docker, ("run", "-d"))[0]


@pytest.mark.asyncio
async def test_pull_policy_if_missing_and_never(
    fake_docker: FakeDockerCLI,
    workspace: Path,
) -> None:
    fake_docker.image_available = False
    backend = LocalDockerBackend(
        RuntimeProfile(name="pull", image="image", pull_policy=PullPolicy.IF_NOT_PRESENT),
        workspace,
        docker=fake_docker,
    )
    await backend.start()
    assert len(_calls(fake_docker, ("pull",))) == 1

    other = FakeDockerCLI()
    other.image_available = False
    never = LocalDockerBackend(
        RuntimeProfile(name="never", image="image", pull_policy=PullPolicy.NEVER),
        workspace / "never",
        docker=other,
    )
    with pytest.raises(Exception, match="image"):
        await never.start()
    assert not _calls(other, ("pull",))


@pytest.mark.asyncio
async def test_owned_close_removes_once(
    profile: RuntimeProfile,
    workspace: Path,
) -> None:
    cli = FakeDockerCLI()
    backend = LocalDockerBackend(profile, workspace, docker=cli)
    await backend.start()
    await backend.close()
    await backend.close()
    assert len(_calls(cli, ("rm", "-f"))) == 1


@pytest.mark.asyncio
async def test_capability_invocation_writes_exact_runtime_ownership_claim(
    profile: RuntimeProfile,
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invocation_id = "a" * 32
    ownership_file = tmp_path / "runtime-owner.json"
    monkeypatch.setenv("OMNICELL_CAPABILITY_INVOCATION_ID", invocation_id)
    monkeypatch.setenv("OMNICELL_RUNTIME_OWNERSHIP_FILE", str(ownership_file))
    cli = FakeDockerCLI()
    backend = LocalDockerBackend(profile, workspace, docker=cli)

    await backend.start()

    claim = json.loads(ownership_file.read_text(encoding="utf-8"))
    assert claim == {
        "invocation_id": invocation_id,
        "container_id": backend.container_id,
        "state": "confirmed",
    }
    run_args = _calls(cli, ("run", "-d"))[0]
    assert (
        f"omnicell.runtime.invocation={invocation_id}" in run_args
    )
    mounts = [
        run_args[index + 1]
        for index, value in enumerate(run_args)
        if value == "--mount"
    ]
    assert len(mounts) == 2
    assert mounts[0].endswith("target=/app/data,readonly")
    assert mounts[1] == (
        f"type=bind,source={workspace}/.omnicell-invocations/{invocation_id},"
        f"target=/app/data/.omnicell-invocations/{invocation_id}"
    )
    assert run_args[run_args.index("--ulimit") + 1] == (
        f"fsize={profile.output_file_max_bytes}:{profile.output_file_max_bytes}"
    )
    assert (workspace / ".omnicell-invocations" / invocation_id).is_dir()

    await backend.close()
    assert not ownership_file.exists()


def test_runtime_ownership_claim_cannot_share_container_data_mount(
    profile: RuntimeProfile,
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OMNICELL_CAPABILITY_INVOCATION_ID", "b" * 32)
    monkeypatch.setenv(
        "OMNICELL_RUNTIME_OWNERSHIP_FILE",
        str(workspace / ".control" / "owner.json"),
    )

    with pytest.raises(RuntimeBackendError, match="容器不可见"):
        LocalDockerBackend(profile, workspace, docker=FakeDockerCLI())


@pytest.mark.asyncio
async def test_active_invocation_output_watchdog_cancels_over_quota_execution(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invocation_id = "9" * 32
    monkeypatch.setenv("OMNICELL_CAPABILITY_INVOCATION_ID", invocation_id)
    cli = FakeDockerCLI()
    cli.block_stream = True
    bounded = RuntimeProfile(
        name="output-quota",
        image="worker",
        output_max_files=2,
        output_file_max_bytes=4,
        output_total_max_bytes=6,
    )
    backend = LocalDockerBackend(bounded, workspace, docker=cli)
    task = asyncio.create_task(backend.execute(["python", "job.py"], timeout=30))
    await cli.stream_started.wait()
    output = workspace / ".omnicell-invocations" / invocation_id
    (output / "too-large.bin").write_bytes(b"12345")

    with pytest.raises(RuntimeBackendError, match="output.*上限"):
        await asyncio.wait_for(task, timeout=2)

    assert cli.stream_cancelled
    assert any(
        "remaining_pids" in " ".join(args)
        for args in _calls(cli, ("exec",))
    )


@pytest.mark.asyncio
async def test_start_failure_cleans_provisional_container_name(
    profile: RuntimeProfile,
    workspace: Path,
) -> None:
    cli = FakeDockerCLI()
    cli.run_error_after_create = asyncio.CancelledError()
    backend = LocalDockerBackend(profile, workspace, docker=cli)

    with pytest.raises(asyncio.CancelledError):
        await backend.start()

    run_args = _calls(cli, ("run", "-d"))[0]
    provisional_name = run_args[run_args.index("--name") + 1]
    assert _calls(cli, ("container", "inspect")) == [
        ("container", "inspect", provisional_name)
    ]
    assert _calls(cli, ("rm", "-f")) == [("rm", "-f", "a" * 64)]
    assert backend.container_id is None


@pytest.mark.asyncio
async def test_start_failure_preserves_provisional_claim_when_container_is_not_visible(
    profile: RuntimeProfile,
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invocation_id = "c" * 32
    ownership_file = tmp_path / "provisional-owner.json"
    monkeypatch.setenv("OMNICELL_CAPABILITY_INVOCATION_ID", invocation_id)
    monkeypatch.setenv("OMNICELL_RUNTIME_OWNERSHIP_FILE", str(ownership_file))
    cli = FakeDockerCLI()
    cli.run_error_after_create = RuntimeError("docker run response lost")
    cli.rm_missing = True
    cli.container_payload["Config"]["Labels"] = {
        "omnicell.runtime.invocation": invocation_id
    }
    backend = LocalDockerBackend(profile, workspace, docker=cli)

    with pytest.raises(RuntimeError, match="response lost") as captured:
        await backend.start()

    assert any("provisional container cleanup failed" in note for note in captured.value.__notes__)
    run_args = _calls(cli, ("run", "-d"))[0]
    provisional_name = run_args[run_args.index("--name") + 1]
    assert backend.container_id == provisional_name
    assert json.loads(ownership_file.read_text(encoding="utf-8")) == {
        "invocation_id": invocation_id,
        "container_id": provisional_name,
        "state": "provisional",
    }

    cli.rm_missing = False
    await backend.close()
    assert backend.container_id is None
    assert not ownership_file.exists()


@pytest.mark.asyncio
async def test_start_failure_keeps_provisional_identity_when_cleanup_needs_retry(
    profile: RuntimeProfile,
    workspace: Path,
) -> None:
    cli = FakeDockerCLI()
    cli.run_error_after_create = RuntimeError("run interrupted after create")
    cli.rm_returncode = 1
    backend = LocalDockerBackend(profile, workspace, docker=cli)

    with pytest.raises(RuntimeError, match="interrupted") as caught:
        await backend.start()

    run_args = _calls(cli, ("run", "-d"))[0]
    provisional_name = run_args[run_args.index("--name") + 1]
    assert backend.container_id == provisional_name
    assert not backend.is_started
    assert any("provisional container cleanup failed" in note for note in caught.value.__notes__)

    cli.rm_returncode = 0
    await backend.close()
    assert backend.container_id is None


@pytest.mark.asyncio
async def test_close_failure_keeps_identity_for_retry(
    profile: RuntimeProfile,
    workspace: Path,
) -> None:
    cli = FakeDockerCLI()
    backend = LocalDockerBackend(profile, workspace, docker=cli)
    await backend.start()
    container_id = backend.container_id
    cli.rm_returncode = 1

    with pytest.raises(DockerCommandError):
        await backend.close()
    assert backend.container_id == container_id
    assert not backend.is_started

    cli.rm_returncode = 0
    recovered = await backend.execute(["python", "-c", "print('recovered')"], timeout=2)
    assert recovered.returncode == 0
    assert backend.container_id == "container-2"
    assert backend.container_id != container_id
    assert len(_calls(cli, ("run", "-d"))) == 2

    await backend.close()
    assert backend.container_id is None


@pytest.mark.asyncio
async def test_close_waits_for_file_operation_before_removing_container(
    profile: RuntimeProfile,
    fake_docker: FakeDockerCLI,
    workspace: Path,
) -> None:
    backend = LocalDockerBackend(profile, workspace, docker=fake_docker)
    await backend.start()

    async with backend._operation_lock:
        close_task = asyncio.create_task(backend.close())
        await asyncio.sleep(0)
        assert not close_task.done()
        assert not _calls(fake_docker, ("rm", "-f"))

    await close_task
    assert len(_calls(fake_docker, ("rm", "-f"))) == 1


@pytest.mark.asyncio
async def test_workspace_identity_and_mount_continue_across_container_replacement(
    profile: RuntimeProfile,
    workspace: Path,
) -> None:
    first_cli = FakeDockerCLI()
    first = LocalDockerBackend(profile, workspace, docker=first_cli)
    await first.start()
    first_metadata = first.metadata()
    await first.close()

    second_cli = FakeDockerCLI()
    second = LocalDockerBackend(profile, workspace, docker=second_cli)
    await second.start()

    assert second.metadata()["workspace"] == first_metadata["workspace"]
    assert str(workspace) in _calls(second_cli, ("run", "-d"))[0][
        _calls(second_cli, ("run", "-d"))[0].index("--mount") + 1
    ]


@pytest.mark.asyncio
async def test_execute_streams_delta_and_checks_first_executable(
    profile: RuntimeProfile,
    fake_docker: FakeDockerCLI,
    workspace: Path,
) -> None:
    backend = LocalDockerBackend(profile, workspace, docker=fake_docker)
    deltas: list[OutputDelta] = []

    result = await backend.execute("python -c 'print(1)'", timeout=2, on_output=deltas.append)

    assert result.stdout == b"stdout"
    assert deltas == [OutputDelta("stdout", b"stdout")]
    stream_args = next(call[1] for call in fake_docker.calls if call[0] == "stream")
    assert stream_args[stream_args.index("--workdir") + 1] == "/app/data"
    assert "/bin/sh" not in stream_args
    assert stream_args[-3:] == ("python", "-c", "print(1)")
    stream_call = next(call for call in fake_docker.calls if call[0] == "stream")
    assert stream_call[2]["stdout_max_bytes"] == profile.stdout_max_bytes
    assert stream_call[2]["stderr_max_bytes"] == profile.stderr_max_bytes
    assert any("remaining_pids" in " ".join(args) for args in _calls(fake_docker, ("exec",)))
    with pytest.raises(CommandNotAllowedError):
        await backend.execute("curl https://example.test", timeout=2)
    with pytest.raises(CommandNotAllowedError):
        await backend.execute("/usr/bin/python -V", timeout=2)
    with pytest.raises(CommandNotAllowedError, match="shell"):
        await backend.execute("python ok.py; curl https://example.test", timeout=2)
    with pytest.raises(CommandNotAllowedError, match="shell"):
        await backend.execute("python ok.py\ncurl https://example.test", timeout=2)
    with pytest.raises(CommandNotAllowedError, match="shell"):
        await backend.execute("python ok.py $(curl https://example.test)", timeout=2)
    with pytest.raises(CommandNotAllowedError, match="NUL"):
        await backend.execute(["python", "bad\x00arg"], timeout=2)

    bounded = RuntimeProfile(
        name="bounded-command",
        image="worker",
        command_max_bytes=16,
    )
    bounded_backend = LocalDockerBackend(bounded, workspace / "bounded", docker=FakeDockerCLI())
    with pytest.raises(CommandNotAllowedError, match="command_max_bytes"):
        await bounded_backend.execute(["python", "x" * 32], timeout=2)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [DockerCommandTimeout("timeout"), OutputLimitExceeded("stdout", 10)],
)
async def test_execute_failure_cleans_container_process_tree(
    error: BaseException,
    profile: RuntimeProfile,
    fake_docker: FakeDockerCLI,
    workspace: Path,
) -> None:
    fake_docker.stream_error = error
    backend = LocalDockerBackend(profile, workspace, docker=fake_docker)

    with pytest.raises(type(error)):
        await backend.execute("python job.py", timeout=1)

    cleanup = [args for args in _calls(fake_docker, ("exec",)) if "remaining_pids" in " ".join(args)]
    assert len(cleanup) == 1
    assert "SIGTERM" in " ".join(cleanup[0])
    assert "SIGKILL" in " ".join(cleanup[0])


@pytest.mark.asyncio
async def test_cancel_active_cancels_host_exec_and_cleans_container_tree(
    profile: RuntimeProfile,
    fake_docker: FakeDockerCLI,
    workspace: Path,
) -> None:
    fake_docker.block_stream = True
    backend = LocalDockerBackend(profile, workspace, docker=fake_docker)
    task = asyncio.create_task(backend.execute("python job.py", timeout=30))
    await fake_docker.stream_started.wait()

    assert await backend.cancel_active()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert fake_docker.stream_cancelled
    assert any("remaining_pids" in " ".join(args) for args in _calls(fake_docker, ("exec",)))


@pytest.mark.asyncio
async def test_close_rejects_operation_already_queued_behind_active_execution(
    profile: RuntimeProfile,
    fake_docker: FakeDockerCLI,
    workspace: Path,
) -> None:
    fake_docker.block_stream = True
    backend = LocalDockerBackend(profile, workspace, docker=fake_docker)
    first = asyncio.create_task(backend.execute(["python", "first.py"], timeout=30))
    await fake_docker.stream_started.wait()
    second = asyncio.create_task(backend.execute(["python", "second.py"], timeout=30))
    await asyncio.sleep(0)

    close_task = asyncio.create_task(backend.close())

    with pytest.raises(asyncio.CancelledError):
        await first
    with pytest.raises(RuntimeBackendError, match="关闭"):
        await second
    await close_task

    assert len([call for call in fake_docker.calls if call[0] == "stream"]) == 1
    assert backend.container_id is None


@pytest.mark.asyncio
async def test_process_cleanup_failure_is_not_silent(
    profile: RuntimeProfile,
    fake_docker: FakeDockerCLI,
    workspace: Path,
) -> None:
    fake_docker.cleanup_returncode = 70
    backend = LocalDockerBackend(profile, workspace, docker=fake_docker)

    with pytest.raises(DockerCommandError, match="remaining_pids"):
        await backend.execute(["python", "job.py"], timeout=2)

    assert backend.container_id is None
    assert not backend.is_started
    assert len(_calls(fake_docker, ("rm", "-f"))) == 1

    fake_docker.cleanup_returncode = 0
    result = await backend.execute(["python", "clean-job.py"], timeout=2)
    assert result.returncode == 0
    assert backend.is_started
    assert len(_calls(fake_docker, ("run", "-d"))) == 2


@pytest.mark.asyncio
async def test_cleanup_and_discard_failure_retains_only_retryable_identity(
    profile: RuntimeProfile,
    workspace: Path,
) -> None:
    cli = FakeDockerCLI()
    cli.cleanup_returncode = 70
    cli.rm_returncode = 1
    backend = LocalDockerBackend(profile, workspace, docker=cli)

    with pytest.raises(DockerCommandError, match="remaining_pids") as caught:
        await backend.execute(["python", "job.py"], timeout=2)

    assert backend.container_id == "container-1"
    assert not backend.is_started
    assert any("unsafe container discard also failed" in note for note in caught.value.__notes__)

    cli.cleanup_returncode = 0
    cli.rm_returncode = 0
    await backend.start()
    assert backend.container_id == "container-2"
    assert backend.is_started


@pytest.mark.asyncio
async def test_operations_are_serialized(
    profile: RuntimeProfile,
    fake_docker: FakeDockerCLI,
    workspace: Path,
) -> None:
    fake_docker.block_stream = True
    backend = LocalDockerBackend(profile, workspace, docker=fake_docker)
    first = asyncio.create_task(backend.execute("python first.py", timeout=30))
    await fake_docker.stream_started.wait()
    second = asyncio.create_task(backend.execute("python second.py", timeout=30))
    await asyncio.sleep(0)

    assert len([call for call in fake_docker.calls if call[0] == "stream"]) == 1
    await backend.cancel_active()
    with pytest.raises(asyncio.CancelledError):
        await first
    second.cancel()
    with pytest.raises(asyncio.CancelledError):
        await second


@pytest.mark.asyncio
async def test_execute_returns_nonzero_business_exit_without_skipping_cleanup(
    profile: RuntimeProfile,
    fake_docker: FakeDockerCLI,
    workspace: Path,
) -> None:
    fake_docker.stream_result = DockerCommandResult(("exec",), 7, b"", b"failed\n")
    backend = LocalDockerBackend(profile, workspace, docker=fake_docker)

    result = await backend.execute(["python", "job.py"], timeout=2)

    assert result.returncode == 7
    assert result.stderr == b"failed\n"
    stream_call = next(call for call in fake_docker.calls if call[0] == "stream")
    assert stream_call[2]["check"] is False
    assert any("remaining_pids" in " ".join(args) for args in _calls(fake_docker, ("exec",)))


@pytest.mark.asyncio
async def test_file_apis_use_resolved_container_paths_and_bounded_reads(
    profile: RuntimeProfile,
    fake_docker: FakeDockerCLI,
    workspace: Path,
) -> None:
    fake_docker.file_responses.update(
        {
            "read": b"abc",
            "list": json.dumps(["a.txt"]).encode(),
            "glob": json.dumps(["results/a.csv"]).encode(),
            "grep": json.dumps([["a.txt", 2, "needle"]]).encode(),
        }
    )
    backend = LocalDockerBackend(profile, workspace, docker=fake_docker)

    await backend.write_bytes("results/a.bin", b"payload")
    assert await backend.read_bytes("results/a.bin", max_bytes=32) == b"abc"
    await backend.ensure_dir("nested")
    assert await backend.list_files() == ("a.txt",)
    assert await backend.glob("**/*.csv") == ("results/a.csv",)
    matches = await backend.grep("needle")
    assert matches[0].path == "a.txt"
    assert matches[0].line == 2

    file_calls = [
        call
        for call in fake_docker.calls
        if call[0] == "run"
        and any(operation in call[1] for operation in ("write", "read", "mkdir", "list", "glob", "grep"))
    ]
    assert all("/app/data" in call[1] for call in file_calls)
    assert all("-T" not in call[1] for call in file_calls)
    write_call = next(call for call in file_calls if "write" in call[1])
    assert write_call[2]["input_data"] == b"payload"
    read_call = next(call for call in file_calls if "read" in call[1])
    assert read_call[2]["stdout_max_bytes"] == 32
    assert "32" in read_call[1]

    with pytest.raises(RuntimePathError):
        await backend.read_bytes("../../etc/passwd")
    with pytest.raises(ValueError):
        await backend.read_bytes("a", max_bytes=profile.read_max_bytes + 1)
    with pytest.raises(ValueError, match="write_max_bytes"):
        await backend.write_bytes("too-large.bin", b"x" * (profile.write_max_bytes + 1))


@pytest.mark.asyncio
async def test_file_cleanup_failure_discards_unsafe_container(
    profile: RuntimeProfile,
    workspace: Path,
) -> None:
    cli = FakeDockerCLI()
    cli.file_responses["read"] = b"payload"
    cli.cleanup_returncode = 70
    backend = LocalDockerBackend(profile, workspace, docker=cli)

    with pytest.raises(DockerCommandError, match="remaining_pids"):
        await backend.read_bytes("result.bin")

    assert backend.container_id is None
    assert not backend.is_started
    assert len(_calls(cli, ("rm", "-f"))) == 1


@pytest.mark.asyncio
async def test_file_control_arguments_use_command_byte_limit(
    workspace: Path,
) -> None:
    profile = RuntimeProfile(
        name="bounded-file-control",
        image="worker",
        command_max_bytes=32,
    )
    backend = LocalDockerBackend(profile, workspace, docker=FakeDockerCLI())

    with pytest.raises(CommandNotAllowedError, match="command_max_bytes"):
        await backend.grep("x" * 64)


@pytest.mark.asyncio
async def test_metadata_is_diagnostic_but_hides_host_path_and_env_values(
    fake_docker: FakeDockerCLI,
    workspace: Path,
) -> None:
    profile = RuntimeProfile(
        name="safe",
        image="worker",
        env={"PUBLIC_TUNING": "unique-private-value"},
    )
    backend = LocalDockerBackend(profile, workspace, docker=fake_docker)
    await backend.start()

    rendered = json.dumps(backend.metadata())
    assert "local-docker-cli" in rendered
    assert "example/worker@sha256:digest" in rendered
    assert "PUBLIC_TUNING" in rendered
    assert "unique-private-value" not in rendered
    assert str(workspace) not in rendered
    assert "workspace-" in rendered


def test_network_and_shell_require_second_policy_authorization(
    workspace: Path,
) -> None:
    network_profile = RuntimeProfile(name="network", image="worker", network="bridge")
    with pytest.raises(CommandNotAllowedError, match="Tool policy"):
        LocalDockerBackend(network_profile, workspace, docker=FakeDockerCLI())

    authorized = LocalDockerBackend(
        network_profile,
        workspace,
        docker=FakeDockerCLI(),
        authorization=RuntimeAuthorization(allow_network=True),
    )
    assert authorized.authorization.allow_network


@pytest.mark.asyncio
async def test_shell_requires_profile_and_tool_policy(
    profile: RuntimeProfile,
    workspace: Path,
) -> None:
    denied = LocalDockerBackend(profile, workspace, docker=FakeDockerCLI())
    with pytest.raises(CommandNotAllowedError, match="Tool policy"):
        await denied.execute(["sh", "-c", "echo denied"], timeout=2)

    allowed = LocalDockerBackend(
        profile,
        workspace / "allowed",
        docker=FakeDockerCLI(),
        authorization=RuntimeAuthorization(allow_shell=True),
    )
    await allowed.execute(["sh", "-c", "echo allowed"], timeout=2)

    dash_profile = RuntimeProfile(
        name="dash-shell",
        image="worker",
        allowed_commands=("python", "dash"),
        shell_commands=("dash",),
    )
    dash_denied = LocalDockerBackend(
        dash_profile,
        workspace / "dash-denied",
        docker=FakeDockerCLI(),
    )
    with pytest.raises(CommandNotAllowedError, match="Tool policy"):
        await dash_denied.execute(["dash", "-c", "echo denied"], timeout=2)
