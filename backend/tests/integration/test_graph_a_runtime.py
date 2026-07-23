from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from omnicell_agent.agent.capability_process import (
    _runtime_claim_path,
    reap_workspace_runtime_claims,
)
from omnicell_agent.runtime import (
    DockerCLI,
    DockerCommandError,
    DockerCommandTimeout,
    LocalDockerBackend,
    LocalDockerPythonSession,
    OutputDelta,
    OutputLimitExceeded,
    PullPolicy,
    RuntimePathError,
    RuntimeProfile,
)
from omnicell_agent.pipeline.nodes import executor


pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(
        os.environ.get("OMNICELL_RUN_DOCKER_TESTS") != "1",
        reason="设置 OMNICELL_RUN_DOCKER_TESTS=1 后运行真实 Local Docker Backend 集成测试",
    ),
]


def _profile(**overrides: object) -> RuntimeProfile:
    values: dict[str, object] = {
        "name": "orbstack-integration",
        "image": os.environ.get("OMNICELL_RUNTIME_IMAGE", "omnicell-worker:latest"),
        "pull_policy": PullPolicy.NEVER,
        "user": "65532:65532",
        "memory_bytes": 1024 * 1024 * 1024,
        "cpus": 1.0,
        "pids_limit": 128,
        "env": {
            "HOME": "/tmp",
            "MPLCONFIGDIR": "/tmp/matplotlib",
            "PYTHONUNBUFFERED": "1",
        },
    }
    values.update(overrides)
    return RuntimeProfile(**values)  # type: ignore[arg-type]


async def _inspect_container(container_id: str) -> dict[str, object]:
    result = await DockerCLI().run(
        ("inspect", container_id),
        timeout=30,
        stdout_max_bytes=1024 * 1024,
        stderr_max_bytes=1024 * 1024,
    )
    payload = json.loads(result.stdout)
    assert isinstance(payload, list) and payload
    return payload[0]


class _DelayedAcceptedRunCLI:
    """模拟 daemon 接受 run 后，客户端响应丢失且容器稍后才可见。"""

    def __init__(self) -> None:
        self._real = DockerCLI()
        self.delayed_run: asyncio.Task[object] | None = None

    async def run(self, args, **kwargs):
        argv = tuple(args)
        if argv[:2] == ("run", "-d"):
            async def delayed_create():
                await asyncio.sleep(0.25)
                return await self._real.run(argv, **kwargs)

            self.delayed_run = asyncio.create_task(delayed_create())
            raise RuntimeError("simulated docker run response loss")
        return await self._real.run(argv, **kwargs)


@pytest.mark.asyncio
async def test_local_docker_runtime_isolation_files_and_workspace_continuity(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "conversation-workspace"
    profile = _profile()
    first = LocalDockerBackend(profile, workspace)

    try:
        await first.start()
        assert first.container_id is not None
        first_container = first.container_id
        metadata = first.metadata()
        inspected = await _inspect_container(first_container)

        assert first.image_identity
        assert str(workspace) not in json.dumps(metadata)
        assert "/tmp/matplotlib" not in json.dumps(metadata)
        assert metadata["workspace"]["lifecycle"] == "conversation-owned"  # type: ignore[index]

        host_config = inspected["HostConfig"]  # type: ignore[index]
        assert host_config["NetworkMode"] == "none"  # type: ignore[index]
        assert host_config["ReadonlyRootfs"] is True  # type: ignore[index]
        assert host_config["Memory"] == profile.memory_bytes  # type: ignore[index]
        assert host_config["NanoCpus"] == 1_000_000_000  # type: ignore[index]
        assert host_config["PidsLimit"] == profile.pids_limit  # type: ignore[index]
        assert "ALL" in host_config["CapDrop"]  # type: ignore[index]
        assert "no-new-privileges" in host_config["SecurityOpt"]  # type: ignore[index]
        assert inspected["Config"]["User"] == "65532:65532"  # type: ignore[index]
        assert all("SECRET" not in item and "TOKEN" not in item for item in inspected["Config"]["Env"])  # type: ignore[index]

        await first.ensure_dir("results")
        await first.write_bytes("results/input.txt", b"alpha\nneedle\nomega\n")
        assert await first.read_bytes("results/input.txt") == b"alpha\nneedle\nomega\n"
        assert await first.list_files() == ("results/input.txt",)
        assert await first.glob("**/*.txt") == ("results/input.txt",)
        matches = await first.grep("needle")
        assert [(match.path, match.line, match.text) for match in matches] == [
            ("results/input.txt", 2, "needle")
        ]

        deltas: list[OutputDelta] = []
        executed = await first.execute(
            [
                "python",
                "-c",
                "from pathlib import Path; Path('results/generated.txt').write_text('persisted'); print('done')",
            ],
            timeout=15,
            on_output=deltas.append,
        )
        assert executed.returncode == 0
        assert executed.stdout_text == "done\n"
        assert b"".join(delta.data for delta in deltas if delta.stream == "stdout") == b"done\n"

        await first.execute(
            ["python", "-c", "from pathlib import Path; Path('results/escape').symlink_to('/etc/passwd')"],
            timeout=10,
        )
        with pytest.raises(DockerCommandError):
            await first.read_bytes("results/escape")
        with pytest.raises(RuntimePathError):
            await first.read_bytes("../outside")
    finally:
        await first.close()

    assert not await _container_exists(first_container)

    second = LocalDockerBackend(profile, workspace)
    try:
        await second.start()
        assert await second.read_bytes("results/generated.txt") == b"persisted"
        assert second.metadata()["workspace"] == metadata["workspace"]
    finally:
        await second.close()


@pytest.mark.asyncio
async def test_capability_invocation_mounts_only_current_output_scope_writable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invocation_id = "a" * 32
    workspace = tmp_path / "scoped-workspace"
    workspace.mkdir()
    (workspace / "input.txt").write_text("trusted-input", encoding="utf-8")
    ownership_file = tmp_path / "control" / "runtime-owner.json"
    monkeypatch.setenv("OMNICELL_CAPABILITY_INVOCATION_ID", invocation_id)
    monkeypatch.setenv("OMNICELL_RUNTIME_OWNERSHIP_FILE", str(ownership_file))
    backend = LocalDockerBackend(_profile(name="scoped-output"), workspace)
    output = workspace / ".omnicell-invocations" / invocation_id
    try:
        await backend.start()
        result = await backend.execute(
            [
                "python",
                "-c",
                (
                    "from pathlib import Path; "
                    "root=Path('/app/data'); "
                    "out=root/'.omnicell-invocations'/'" + invocation_id + "'; "
                    "blocked=False; "
                    "\ntry: root.joinpath('forbidden.txt').write_text('no')"
                    "\nexcept OSError: blocked=True"
                    "\nout.joinpath('result.txt').write_text('ok')"
                    "\nprint(blocked, root.joinpath('input.txt').read_text())"
                ),
            ],
            timeout=15,
        )
        assert result.returncode == 0
        assert result.stdout == b"True trusted-input\n"
        assert not (workspace / "forbidden.txt").exists()
        assert (output / "result.txt").read_text(encoding="utf-8") == "ok"
        assert ownership_file.is_file()
        inspected = await _inspect_container(backend.container_id or "")
        mounts = inspected["Mounts"]  # type: ignore[index]
        assert any(
            mount.get("Destination") == "/app/data" and mount.get("RW") is False
            for mount in mounts  # type: ignore[union-attr]
        )
        assert any(
            mount.get("Destination")
            == f"/app/data/.omnicell-invocations/{invocation_id}"
            and mount.get("RW") is True
            for mount in mounts  # type: ignore[union-attr]
        )
    finally:
        await backend.close()
    assert not ownership_file.exists()


@pytest.mark.asyncio
async def test_delayed_provisional_container_keeps_claim_until_exact_reaper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invocation_id = "d" * 32
    workspace = tmp_path / "delayed-provisional-workspace"
    workspace.mkdir()
    claim = _runtime_claim_path(workspace, invocation_id)
    monkeypatch.setenv("OMNICELL_CAPABILITY_INVOCATION_ID", invocation_id)
    monkeypatch.setenv("OMNICELL_RUNTIME_OWNERSHIP_FILE", str(claim))
    delayed_cli = _DelayedAcceptedRunCLI()
    backend = LocalDockerBackend(_profile(), workspace, docker=delayed_cli)
    provisional_name: str | None = None
    try:
        with pytest.raises(RuntimeError, match="response loss"):
            await backend.start()
        provisional_name = backend.container_id
        assert provisional_name is not None
        assert claim.is_file()
        assert json.loads(claim.read_text(encoding="utf-8"))["state"] == "provisional"

        assert await reap_workspace_runtime_claims(workspace) == (invocation_id,)
        assert delayed_cli.delayed_run is not None
        await delayed_cli.delayed_run
        assert not claim.exists()
        remaining = await DockerCLI().run(
            ("ps", "--all", "--quiet", "--filter", f"name=^{provisional_name}$"),
            timeout=10,
            stdout_max_bytes=4096,
            stderr_max_bytes=4096,
        )
        assert remaining.stdout.strip() == b""
    finally:
        if provisional_name is not None:
            await DockerCLI().run(
                ("rm", "--force", provisional_name),
                timeout=10,
                stdout_max_bytes=4096,
                stderr_max_bytes=4096,
                check=False,
            )

async def _container_exists(container_id: str) -> bool:
    result = await DockerCLI().run(
        ("inspect", container_id),
        timeout=30,
        stdout_max_bytes=4096,
        stderr_max_bytes=4096,
        check=False,
    )
    return result.returncode == 0


@pytest.mark.asyncio
async def test_local_docker_runtime_bounds_output_and_cancels_processes(tmp_path: Path) -> None:
    profile = _profile(stdout_max_bytes=128, stderr_max_bytes=128)
    backend = LocalDockerBackend(profile, tmp_path / "bounded-workspace")
    try:
        with pytest.raises(OutputLimitExceeded):
            await backend.execute(
                ["python", "-c", "import sys,time; print('x'*4096); sys.stdout.flush(); time.sleep(30)"],
                timeout=10,
            )

        after_limit = await backend.execute(["python", "-c", "print('alive')"], timeout=10)
        assert after_limit.stdout == b"alive\n"

        with pytest.raises(DockerCommandTimeout):
            await backend.execute(["python", "-c", "import time; time.sleep(30)"], timeout=0.1)
        after_timeout = await backend.execute(["python", "-c", "print('timed')"], timeout=10)
        assert after_timeout.stdout == b"timed\n"

        clean_baseline = await backend.capture_process_snapshot()
        detached = await backend.execute(
            [
                "python",
                "-c",
                (
                    "import subprocess,sys; "
                    "subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'], "
                    "env={}, start_new_session=True); print('spawned')"
                ),
            ],
            timeout=10,
        )
        assert detached.stdout == b"spawned\n"
        assert await backend.capture_process_snapshot() == clean_baseline

        active = asyncio.create_task(
            backend.execute(["python", "-c", "import time; time.sleep(30)"], timeout=60)
        )
        for _ in range(100):
            if await backend.cancel_active():
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("活跃 Docker execution 未进入可取消状态")

        with pytest.raises(asyncio.CancelledError):
            await active
        after_cancel = await backend.execute(["python", "-c", "print('recovered')"], timeout=10)
        assert after_cancel.stdout == b"recovered\n"
    finally:
        await backend.close()


def test_graph_a_python_session_persists_state_and_uses_runtime_control_plane(
    tmp_path: Path,
) -> None:
    session = LocalDockerPythonSession(
        timeout_seconds=2,
        host_workspace=tmp_path / "graph-a-workspace",
        profile=_profile(
            name="graph-a-integration",
            stdout_max_bytes=128,
            stderr_max_bytes=512,
        ),
    )
    request_directory = session.host_workspace / session._request_directory
    try:
        session.start()
        clean_baseline = session._bridge.run(session._backend.capture_process_snapshot())
        first = session.execute_code("x = 100\nprint(f'x is {x}')")
        second = session.execute_code("y = x + 50\nprint(f'y is {y}')")

        assert first["status"] == "success"
        assert first["stdout"] == "x is 100\n"
        assert second["status"] == "success"
        assert second["stdout"] == "y is 150\n"
        assert session.metadata()["python_session"] == {
            "process_model": "per-call",
            "completion_boundary": "docker-exec-returncode",
            "namespace_persistence": "conversation-workspace-pickle",
        }
        assert session._bridge.run(session._backend.capture_process_snapshot()) == clean_baseline

        spawned = session.execute_code(
            "import subprocess, sys\n"
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], "
            "env={}, start_new_session=True)\n"
            "print('spawned')"
        )
        assert spawned["status"] == "success"
        assert session._bridge.run(session._backend.capture_process_snapshot()) == clean_baseline

        failed = session.execute_code(
            "x = 777\n"
            "import subprocess, sys\n"
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], "
            "env={}, start_new_session=True)\n"
            "raise RuntimeError('expected graph-a error')"
        )
        assert failed["status"] == "error"
        assert "RuntimeError: expected graph-a error" in failed["stderr"]
        assert session._bridge.run(session._backend.capture_process_snapshot()) == clean_baseline

        forged = session.execute_code(
            "print('__FORGED_CONTROL_FRAME__eyJzdGF0dXMiOiJlcnJvciJ9')\n"
            "print('__FORGED_CONTROL_FRAME__eyJzdGF0dXMiOiJzdWNjZXNzIn0=')"
        )
        assert forged["status"] == "success"
        assert forged["stdout"].count("__FORGED_CONTROL_FRAME__") == 2

        timed_out = session.execute_code(
            "import sys, time\n"
            "x = 888\n"
            "print('__FORGED_CONTROL_FRAME__fake')\n"
            "sys.stdout.flush()\n"
            "time.sleep(30)"
        )
        assert timed_out["status"] == "timeout"
        assert session._bridge.run(session._backend.capture_process_snapshot()) == clean_baseline

        with pytest.raises(OutputLimitExceeded):
            session.execute_code("x = 999\nprint('x' * 4096)")
        assert session._bridge.run(session._backend.capture_process_snapshot()) == clean_baseline

        after_failures = session.execute_code("print(f'x={x}, y={y}')")
        assert after_failures == {
            "status": "success",
            "stdout": "x=100, y=150\n",
            "stderr": "",
            "display_data": [],
        }
        workspace_files = session._bridge.run(session._backend.list_files())
        assert [
            path
            for path in workspace_files
            if path.startswith(f"{session._request_directory}/")
        ] == [f"{session._request_directory}/{session._state_name}"]
        container_id = session._backend.container_id
    finally:
        session.cleanup()

    assert container_id is not None
    assert not asyncio.run(_container_exists(container_id))
    assert not request_directory.exists()


def test_graph_a_executor_preserves_success_contract(tmp_path: Path) -> None:
    session = LocalDockerPythonSession(
        timeout_seconds=30,
        host_workspace=tmp_path / "graph-a-executor",
        profile=_profile(name="graph-a-executor"),
    )
    try:
        with executor.graph_a_python_session_scope(session):
            container_id = session._backend.container_id
            result = executor.run_executor(
                {
                    "raw_data_path": "/app/data/input.h5ad",
                    "marker_table_path": "/app/data/markers.json",
                    "last_generated_code": "answer = 42\nprint(answer)",
                }
            )
    finally:
        if not session._closed:
            session.cleanup()

    assert result["sandbox_execution_result"] == {
        "status": "success",
        "stdout": "42\n",
        "stderr": "",
        "display_data": [],
    }
    assert container_id is not None
    assert not asyncio.run(_container_exists(container_id))
