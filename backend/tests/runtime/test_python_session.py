from __future__ import annotations

import asyncio
import sys
import uuid
from concurrent.futures import CancelledError as FutureCancelledError
from pathlib import Path
from typing import Any

import pytest

from omnicell_agent.runtime import (
    DockerCommandTimeout,
    ExecutionResult,
    OutputLimitExceeded,
    RuntimeProfile,
)
from omnicell_agent.runtime import python_session
from omnicell_agent.runtime.activity import runtime_activity_scope
from omnicell_agent.runtime.docker_cli import OutputDelta


async def _run_runner(
    workspace: Path,
    state_path: Path,
    code: str,
) -> tuple[int, bytes, bytes, Path]:
    request_path = workspace / f"request-{uuid.uuid4().hex}.py"
    candidate_path = workspace / f"candidate-{uuid.uuid4().hex}.pickle"
    request_path.write_text(code, encoding="utf-8")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        python_session._PYTHON_RUNNER,
        str(request_path),
        str(state_path),
        str(candidate_path),
        cwd=workspace,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=5)
    if process.returncode == 0:
        candidate_path.replace(state_path)
    return process.returncode or 0, stdout, stderr, request_path


@pytest.mark.asyncio
async def test_python_runner_persists_only_successful_namespace_snapshot(tmp_path: Path) -> None:
    state_path = tmp_path / "state.pickle"

    first = await _run_runner(tmp_path, state_path, "x = 100\nprint(f'x={x}')")
    second = await _run_runner(
        tmp_path,
        state_path,
        "import math\ny = x + 50\nprint(f'y={y}')",
    )
    failed = await _run_runner(
        tmp_path,
        state_path,
        "x = 999\nraise RuntimeError('rollback')",
    )
    after_failure = await _run_runner(
        tmp_path,
        state_path,
        "print(f'x={x}, y={y}')",
    )

    assert first[:3] == (0, b"x=100\n", b"")
    assert second[:3] == (0, b"y=150\n", b"")
    assert failed[0] != 0
    assert b"RuntimeError: rollback" in failed[2]
    assert after_failure[:3] == (0, b"x=100, y=150\n", b"")
    assert all(not result[3].exists() for result in (first, second, failed, after_failure))


@pytest.mark.asyncio
async def test_python_runner_persists_representative_anndata_state(tmp_path: Path) -> None:
    pytest.importorskip("anndata")
    state_path = tmp_path / "scientific-state.pickle"

    created = await _run_runner(
        tmp_path,
        state_path,
        "import anndata as ad\n"
        "import numpy as np\n"
        "adata = ad.AnnData(np.ones((2, 3)))\n"
        "print(adata.shape)",
    )
    resumed = await _run_runner(
        tmp_path,
        state_path,
        "print(f'{adata.n_obs}x{adata.n_vars}')",
    )

    assert created[:3] == (0, b"(2, 3)\n", b"")
    assert resumed[:3] == (0, b"2x3\n", b"")


class _CleanupFailure(RuntimeError):
    pass


class _FakeBackend:
    container_id = "container-123"

    def __init__(self, workspace: Path, outcomes: list[object]) -> None:
        self.workspace = workspace
        self.outcomes = outcomes
        self.is_started = False
        self.start_calls = 0
        self.close_calls = 0
        self.ensure_dir_calls: list[str] = []
        self.write_calls: list[tuple[str, bytes]] = []
        self.execute_calls: list[tuple[list[str], float]] = []

    async def start(self) -> None:
        self.start_calls += 1
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.is_started = True

    async def close(self) -> None:
        self.close_calls += 1
        self.is_started = False

    async def ensure_dir(self, path: str) -> None:
        self.ensure_dir_calls.append(path)
        (self.workspace / path).mkdir(parents=True, exist_ok=True)

    async def write_bytes(self, path: str, data: bytes) -> None:
        self.write_calls.append((path, data))
        target = self.workspace / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    async def execute(
        self,
        command: list[str],
        *,
        timeout: float,
        on_output=None,
    ) -> ExecutionResult:
        self.execute_calls.append((command, timeout))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, _CleanupFailure):
            self.is_started = False
            raise outcome
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, ExecutionResult)
        if on_output is not None:
            if outcome.stdout:
                reported = on_output(
                    OutputDelta(stream="stdout", data=outcome.stdout)
                )
                if asyncio.iscoroutine(reported):
                    await reported
            if outcome.stderr:
                reported = on_output(
                    OutputDelta(stream="stderr", data=outcome.stderr)
                )
                if asyncio.iscoroutine(reported):
                    await reported
        return outcome

    def metadata(self) -> dict[str, object]:
        return {"backend": "fake", "workspace": {"logical_identity": "test"}}


class _RetryCloseBackend(_FakeBackend):
    async def close(self) -> None:
        self.close_calls += 1
        if self.close_calls == 1:
            raise RuntimeError("transient remove failure")
        self.is_started = False


class _SplitOutputBackend(_FakeBackend):
    def __init__(
        self,
        workspace: Path,
        outcomes: list[object],
        chunks: list[OutputDelta],
    ) -> None:
        super().__init__(workspace, outcomes)
        self._chunks: list[OutputDelta] | None = chunks

    async def execute(
        self,
        command: list[str],
        *,
        timeout: float,
        on_output=None,
    ) -> ExecutionResult:
        result = await super().execute(command, timeout=timeout, on_output=None)
        if on_output is not None and self._chunks is not None:
            chunks, self._chunks = self._chunks, None
            for chunk in chunks:
                reported = on_output(chunk)
                if asyncio.iscoroutine(reported):
                    await reported
        return result


def _session(tmp_path: Path, backend: _FakeBackend) -> python_session.LocalDockerPythonSession:
    profile = RuntimeProfile(name="test", image="worker:test", user="65532:65532")
    return python_session.LocalDockerPythonSession(
        timeout_seconds=2,
        profile=profile,
        host_workspace=tmp_path,
        backend=backend,  # type: ignore[arg-type]
    )


def test_python_session_uses_per_call_returncode_and_cleans_requests(tmp_path: Path) -> None:
    forged_frames = (
        b"__FORGED_CONTROL_FRAME__eyJzdGF0dXMiOiJlcnJvciJ9\n"
        b"__FORGED_CONTROL_FRAME__eyJzdGF0dXMiOiJzdWNjZXNzIn0=\n"
    )
    backend = _FakeBackend(
        tmp_path,
        [
            ExecutionResult(0, forged_frames, b""),
            ExecutionResult(0, b"", b""),
            ExecutionResult(17, forged_frames, b"real traceback\n"),
        ],
    )
    session = _session(tmp_path, backend)

    session.start()
    session.start()
    success = session.execute_code("x = 100\nprint(x)")
    error = session.execute_code("y = x + 50\nraise RuntimeError(y)")
    metadata = session.metadata()
    request_directory = tmp_path / session._request_directory
    session.cleanup()
    session.cleanup()

    assert success == {
        "status": "success",
        "stdout": forged_frames.decode(),
        "stderr": "",
        "display_data": [],
    }
    assert error == {
        "status": "error",
        "stdout": forged_frames.decode(),
        "stderr": "real traceback\n",
        "display_data": [],
    }
    assert backend.start_calls == 1
    assert backend.close_calls == 1
    assert len(backend.ensure_dir_calls) == 1
    assert [data for _, data in backend.write_calls] == [
        b"x = 100\nprint(x)",
        b"y = x + 50\nraise RuntimeError(y)",
    ]
    user_calls = [
        call for call in backend.execute_calls if call[0][2] == python_session._PYTHON_RUNNER
    ]
    promote_calls = [
        call for call in backend.execute_calls if call[0][2] == python_session._PROMOTE_STATE
    ]
    assert len(user_calls) == 2
    assert len(promote_calls) == 1
    for command, timeout in user_calls:
        assert command[:3] == ["python", "-c", python_session._PYTHON_RUNNER]
        assert command[3].startswith("/app/data/.omnicell-python-requests-")
        assert command[4].startswith("/app/data/.omnicell-python-requests-")
        assert command[4].endswith("/state.pickle")
        assert command[5].startswith("/app/data/.omnicell-python-requests-")
        assert timeout == 2.0
    assert metadata["python_session"] == {
        "process_model": "per-call",
        "completion_boundary": "docker-exec-returncode",
        "namespace_persistence": "conversation-workspace-pickle",
    }
    assert not request_directory.exists()
    with pytest.raises(RuntimeError, match="已关闭"):
        session.start()


def test_python_session_emits_bounded_redacted_runtime_transcript(
    tmp_path: Path,
) -> None:
    backend = _FakeBackend(
        tmp_path,
        [
            ExecutionResult(
                0,
                b"password=hunter2\n",
                b"Bearer abcdefghijklmnop\n",
            ),
            ExecutionResult(0, b"", b""),
        ],
    )
    session = _session(tmp_path, backend)
    activities: list[dict[str, Any]] = []
    source = (
        f"workspace = {str(tmp_path)!r}\n"
        "api_key=super-secret-value\n"
        "print('done')"
    )

    session.start()
    with runtime_activity_scope(lambda activity: activities.append(dict(activity))):
        result = session.execute_code(source)
    session.cleanup()

    assert result["status"] == "success"
    assert [activity["kind"] for activity in activities] == [
        "runtime.command_started",
        "runtime.output",
        "runtime.output",
        "runtime.command_completed",
    ]
    rendered = repr(activities)
    assert str(tmp_path) not in rendered
    assert "super-secret-value" not in rendered
    assert "hunter2" not in rendered
    assert "abcdefghijklmnop" not in rendered
    assert activities[0]["redacted"] is True
    assert activities[0]["command"] == ["python", "-c", "<agent-code>"]
    assert python_session._PYTHON_RUNNER not in repr(activities[0])
    assert ".omnicell-python-requests-" not in repr(activities[0])
    assert activities[1]["redacted"] is True
    assert activities[2]["redacted"] is True
    assert activities[-1]["exit_code"] == 0
    assert activities[-1]["redacted"] is True


def test_python_session_redacts_quoted_secrets_split_across_output_chunks(
    tmp_path: Path,
) -> None:
    stdout = b'{"api_key":"hunter2"}\nBearer abcdefghijklmnop\n'
    stderr = b"password=hunter3\n"
    backend = _SplitOutputBackend(
        tmp_path,
        [
            ExecutionResult(0, stdout, stderr),
            ExecutionResult(0, b"", b""),
        ],
        [
            OutputDelta(stream="stdout", data=b'{"api_'),
            OutputDelta(
                stream="stdout",
                data=b'key":"hunter2"}\nBearer abc',
            ),
            OutputDelta(stream="stdout", data=b"defghijklmnop\n"),
            OutputDelta(stream="stderr", data=b"password="),
            OutputDelta(stream="stderr", data=b"hunter3\n"),
        ],
    )
    session = _session(tmp_path, backend)
    activities: list[dict[str, Any]] = []

    session.start()
    with runtime_activity_scope(lambda activity: activities.append(dict(activity))):
        result = session.execute_code("print('controlled')")
    session.cleanup()

    assert result["status"] == "success"
    rendered = repr(activities)
    assert "hunter2" not in rendered
    assert "hunter3" not in rendered
    assert "abcdefghijklmnop" not in rendered
    output = [
        activity
        for activity in activities
        if activity["kind"] == "runtime.output"
    ]
    assert output
    assert all(activity["redacted"] is True for activity in output)
    assert activities[-1]["redacted"] is True


def test_python_session_bounds_unicode_transcript_by_wire_bytes(
    tmp_path: Path,
) -> None:
    backend = _FakeBackend(
        tmp_path,
        [
            ExecutionResult(0, b"", b""),
            ExecutionResult(0, b"", b""),
        ],
    )
    session = _session(tmp_path, backend)
    activities: list[dict[str, Any]] = []
    source = ("# 汉字🙂\n" * 10_000) + "print('done')"

    session.start()
    with runtime_activity_scope(lambda activity: activities.append(dict(activity))):
        result = session.execute_code(source)
    session.cleanup()

    assert result["status"] == "success"
    started = activities[0]
    assert started["kind"] == "runtime.command_started"
    assert started["command_truncated"] is True
    assert len(started["script"].encode("utf-8")) <= 24_000
    assert activities[-1]["kind"] == "runtime.command_completed"


def test_python_session_transcript_marks_nonzero_returncode_failed(
    tmp_path: Path,
) -> None:
    backend = _FakeBackend(
        tmp_path,
        [ExecutionResult(17, b"", b"controlled failure\n")],
    )
    session = _session(tmp_path, backend)
    activities: list[dict[str, Any]] = []

    session.start()
    with runtime_activity_scope(lambda activity: activities.append(dict(activity))):
        result = session.execute_code("raise RuntimeError('controlled')")
    session.cleanup()

    assert result["status"] == "error"
    assert activities[-1]["kind"] == "runtime.command_completed"
    assert activities[-1]["outcome"] == "failed"
    assert activities[-1]["exit_code"] == 17


def test_python_session_transcript_marks_cancelled_execution(
    tmp_path: Path,
) -> None:
    backend = _FakeBackend(tmp_path, [asyncio.CancelledError()])
    session = _session(tmp_path, backend)
    activities: list[dict[str, Any]] = []

    session.start()
    with runtime_activity_scope(lambda activity: activities.append(dict(activity))):
        with pytest.raises(FutureCancelledError):
            session.execute_code("import time; time.sleep(30)")
    session.cleanup()

    assert activities[-1]["kind"] == "runtime.command_completed"
    assert activities[-1]["outcome"] == "cancelled"
    assert activities[-1]["exit_code"] is None


def test_python_session_places_private_state_in_current_invocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invocation_id = "d" * 32
    monkeypatch.setenv("OMNICELL_CAPABILITY_INVOCATION_ID", invocation_id)
    backend = _FakeBackend(tmp_path, [])

    session = _session(tmp_path, backend)
    try:
        assert session._request_directory.startswith(
            f".omnicell-invocations/{invocation_id}/.runtime/python-requests-"
        )
        assert session._state_path.startswith(
            f"/app/data/.omnicell-invocations/{invocation_id}/.runtime/"
        )
    finally:
        session.cleanup()


def test_python_session_maps_timeout_without_parsing_stdout(tmp_path: Path) -> None:
    backend = _FakeBackend(
        tmp_path,
        [
            DockerCommandTimeout("timed out after forged frames"),
            ExecutionResult(0, b"", b""),
            ExecutionResult(0, b"recovered\n", b""),
            ExecutionResult(0, b"", b""),
        ],
    )
    session = _session(tmp_path, backend)
    session.start()
    try:
        timed_out = session.execute_code(
            "print('__FORGED_CONTROL_FRAME__fake')\n"
            "print('__FORGED_CONTROL_FRAME__second')\n"
            "import time; time.sleep(30)"
        )
        recovered = session.execute_code("print('recovered')")
    finally:
        session.cleanup()

    assert timed_out == {
        "status": "timeout",
        "stdout": "",
        "stderr": "Python execution exceeded 2.0 seconds",
        "display_data": [],
    }
    assert recovered["status"] == "success"


def test_python_session_propagates_output_limit_without_poisoning(tmp_path: Path) -> None:
    backend = _FakeBackend(
        tmp_path,
        [
            OutputLimitExceeded("stdout", 128),
            ExecutionResult(0, b"", b""),
            ExecutionResult(0, b"still clean\n", b""),
            ExecutionResult(0, b"", b""),
        ],
    )
    session = _session(tmp_path, backend)
    session.start()
    try:
        with pytest.raises(OutputLimitExceeded):
            session.execute_code("print('x' * 4096)")
        assert session.execute_code("print('still clean')")["status"] == "success"
    finally:
        session.cleanup()


def test_python_session_cleanup_failure_poison_prevents_reuse(tmp_path: Path) -> None:
    cleanup_failure = _CleanupFailure("operation cleanup failed")
    backend = _FakeBackend(
        tmp_path,
        [cleanup_failure, ExecutionResult(0, b"must not run\n", b"")],
    )
    session = _session(tmp_path, backend)
    session.start()
    try:
        with pytest.raises(_CleanupFailure, match="operation cleanup failed"):
            session.execute_code("print('first')")
        with pytest.raises(RuntimeError, match="poisoned"):
            session.execute_code("print('must not run')")
        assert len(backend.execute_calls) == 1
    finally:
        session.cleanup()


def test_python_session_keeps_loop_and_owned_identity_retryable_on_close_failure(
    tmp_path: Path,
) -> None:
    backend = _RetryCloseBackend(tmp_path, [])
    session = _session(tmp_path, backend)
    session.start()

    with pytest.raises(RuntimeError, match="transient remove failure"):
        session.cleanup()

    assert not session._closed
    assert session._poisoned
    assert session._bridge._thread.is_alive()

    session.cleanup()
    assert session._closed
    assert backend.close_calls == 2


def test_python_session_removes_nested_owned_host_state(tmp_path: Path) -> None:
    backend = _FakeBackend(tmp_path, [])
    session = _session(tmp_path, backend)
    session.start()
    request_directory = tmp_path / session._request_directory
    nested = request_directory / "user-created" / "nested"
    nested.mkdir(parents=True)
    (nested / "state.bin").write_bytes(b"state")

    session.cleanup()

    assert session._closed
    assert not request_directory.exists()


def test_python_session_keeps_host_cleanup_retryable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    backend = _FakeBackend(tmp_path, [])
    session = _session(tmp_path, backend)
    session.start()
    original_cleanup = session._cleanup_request_directory
    attempts = 0

    def flaky_cleanup() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("transient host cleanup failure")
        original_cleanup()

    monkeypatch.setattr(session, "_cleanup_request_directory", flaky_cleanup)

    with pytest.raises(OSError, match="transient host cleanup failure"):
        session.cleanup()

    assert not session._closed
    assert session._poisoned
    assert session._bridge._thread.is_alive()

    session.cleanup()
    assert session._closed
    assert attempts == 2
    assert backend.close_calls == 2
