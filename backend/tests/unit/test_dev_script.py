from __future__ import annotations

import io
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import call, patch

import pytest


_SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts" / "dev.py"
_SPEC = importlib.util.spec_from_file_location("omnicell_dev_script", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
dev = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = dev
_SPEC.loader.exec_module(dev)


def _backend_service() -> dev.Service:
    return next(
        service for service in dev.SERVICES if service.name == "backend"
    )


class _SocketConnection:
    def __enter__(self) -> "_SocketConnection":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class _HttpResponse:
    def __init__(self, payload: object) -> None:
        self._body = json.dumps(payload).encode()

    def __enter__(self) -> "_HttpResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, _limit: int) -> bytes:
        return self._body


def test_probe_backend_reports_not_running_when_port_is_closed() -> None:
    with patch.object(
        dev.socket,
        "create_connection",
        side_effect=ConnectionRefusedError,
    ):
        assert (
            dev._probe_backend("http://127.0.0.1:8000")
            is dev.BackendProbeState.NOT_RUNNING
        )


def test_probe_backend_recognizes_omnicell_liveness() -> None:
    response = _HttpResponse({"schema_version": 1, "status": "alive"})
    with (
        patch.object(
            dev.socket,
            "create_connection",
            return_value=_SocketConnection(),
        ),
        patch.object(dev, "urlopen", return_value=response),
    ):
        assert (
            dev._probe_backend("http://127.0.0.1:8000")
            is dev.BackendProbeState.OMNICELL_RUNNING
        )


def test_probe_backend_rejects_an_unrecognized_service() -> None:
    response = _HttpResponse({"status": "ok"})
    with (
        patch.object(
            dev.socket,
            "create_connection",
            return_value=_SocketConnection(),
        ),
        patch.object(dev, "urlopen", return_value=response),
    ):
        assert (
            dev._probe_backend("http://127.0.0.1:8000")
            is dev.BackendProbeState.PORT_OCCUPIED
        )


def test_services_to_start_restarts_an_existing_backend() -> None:
    with (
        patch.object(
            dev,
            "_probe_backend",
            return_value=dev.BackendProbeState.OMNICELL_RUNNING,
        ),
        patch.object(dev, "_stop_existing_backend") as stop_existing,
    ):
        services = dev._services_to_start()

    assert [service.name for service in services] == ["backend", "frontend"]
    stop_existing.assert_called_once_with("http://127.0.0.1:8000")


def test_services_to_start_includes_backend_when_port_is_closed() -> None:
    with patch.object(
        dev,
        "_probe_backend",
        return_value=dev.BackendProbeState.NOT_RUNNING,
    ):
        services = dev._services_to_start()

    assert [service.name for service in services] == ["backend", "frontend"]


def test_services_to_start_rejects_an_unrecognized_port_owner() -> None:
    with patch.object(
        dev,
        "_probe_backend",
        return_value=dev.BackendProbeState.PORT_OCCUPIED,
    ):
        with pytest.raises(RuntimeError, match="未识别为 OmniCell API"):
            dev._services_to_start()


def test_stop_existing_backend_terminates_only_verified_listener_pids() -> None:
    output = io.StringIO()
    with (
        patch.object(dev, "_listener_pids", return_value=(123, 456)),
        patch.object(dev, "_is_project_backend_process", return_value=True),
        patch.object(dev, "_wait_for_backend_stop", return_value=True),
        patch.object(dev.os, "kill") as kill,
        patch("sys.stdout", output),
    ):
        dev._stop_existing_backend("http://127.0.0.1:8000")

    assert kill.call_args_list == [
        call(123, dev.signal.SIGTERM),
        call(456, dev.signal.SIGTERM),
    ]
    assert "准备重新启动" in output.getvalue()


def test_stop_existing_backend_refuses_foreign_listener() -> None:
    with (
        patch.object(dev, "_listener_pids", return_value=(123,)),
        patch.object(
            dev,
            "_is_project_backend_process",
            return_value=False,
        ),
        patch.object(dev.os, "kill") as kill,
    ):
        with pytest.raises(RuntimeError, match="不属于当前仓库"):
            dev._stop_existing_backend("http://127.0.0.1:8000")

    kill.assert_not_called()


def test_stop_existing_backend_escalates_only_the_same_verified_pids() -> None:
    with (
        patch.object(dev, "_listener_pids", return_value=(123,)),
        patch.object(dev, "_is_project_backend_process", return_value=True),
        patch.object(
            dev,
            "_wait_for_backend_stop",
            side_effect=(False, True),
        ),
        patch.object(dev.os, "kill") as kill,
    ):
        dev._stop_existing_backend("http://127.0.0.1:8000")

    assert kill.call_args_list == [
        call(123, dev.signal.SIGTERM),
        call(123, dev.signal.SIGKILL),
    ]


def test_project_backend_process_accepts_current_repository_cwd() -> None:
    with (
        patch.object(dev, "_process_cwd", return_value=dev.ROOT),
        patch.object(dev, "_process_command", return_value="") as command,
    ):
        assert dev._is_project_backend_process(123)

    command.assert_not_called()


def test_project_backend_process_rejects_unrelated_process() -> None:
    with (
        patch.object(
            dev,
            "_process_cwd",
            return_value=Path("/private/tmp"),
        ),
        patch.object(
            dev,
            "_process_command",
            return_value="/usr/bin/python unrelated_server.py",
        ),
    ):
        assert not dev._is_project_backend_process(123)


def test_backend_service_runs_the_current_checkout_module() -> None:
    backend = _backend_service()

    assert backend.command == (
        str(dev.BACKEND_PYTHON),
        "-m",
        "omnicell_agent.api.cli",
    )


def test_backend_environment_prepends_the_current_source_tree() -> None:
    with patch.dict(
        dev.os.environ,
        {"PYTHONPATH": "/existing/source"},
        clear=True,
    ):
        environment = dev._service_environment(_backend_service())

    assert environment["PYTHONPATH"] == (
        f"{dev.BACKEND_SOURCE}{dev.os.pathsep}/existing/source"
    )


def test_backend_preflight_imports_from_the_current_checkout() -> None:
    backend = _backend_service()
    completed = SimpleNamespace(returncode=0)
    with (
        patch.object(dev.shutil, "which", return_value=str(dev.BACKEND_PYTHON)),
        patch.object(
            dev.subprocess,
            "run",
            return_value=completed,
        ) as run,
    ):
        dev._check_prerequisites((backend,))

    command = run.call_args.args[0]
    environment = run.call_args.kwargs["env"]
    assert command == (
        str(dev.BACKEND_PYTHON),
        "-c",
        "import omnicell_agent.api.bootstrap",
    )
    assert environment["PYTHONPATH"].split(dev.os.pathsep)[0] == str(
        dev.BACKEND_SOURCE
    )


def test_backend_preflight_reports_a_controlled_sync_hint() -> None:
    backend = _backend_service()
    completed = SimpleNamespace(returncode=1)
    with (
        patch.object(dev.shutil, "which", return_value=str(dev.BACKEND_PYTHON)),
        patch.object(dev.subprocess, "run", return_value=completed),
    ):
        with pytest.raises(RuntimeError, match="uv sync"):
            dev._check_prerequisites((backend,))


def test_main_preflights_before_attempting_to_stop_the_old_backend() -> None:
    failure = RuntimeError("preflight failed")
    with (
        patch.object(dev, "_check_prerequisites", side_effect=failure),
        patch.object(dev, "_services_to_start") as services_to_start,
    ):
        with pytest.raises(RuntimeError, match="preflight failed"):
            dev.main()

    services_to_start.assert_not_called()
