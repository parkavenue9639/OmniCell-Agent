"""同时启动并可靠回收 OmniCell-Agent 本地前后端服务。"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from threading import Event
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
BACKEND_SOURCE = ROOT / "backend" / "src"
BACKEND_PYTHON = ROOT / ".venv" / "bin" / "python"
SHUTDOWN_TIMEOUT_SECONDS = 10.0
BACKEND_PROBE_TIMEOUT_SECONDS = 0.75
BACKEND_STOP_POLL_SECONDS = 0.1
BACKEND_LIVENESS_PATH = "/api/v1/health/live"
BACKEND_COMMAND_MARKERS = ("omnicell-api", "omnicell_agent.api")


@dataclass(frozen=True, slots=True)
class Service:
    name: str
    command: tuple[str, ...]
    cwd: Path


SERVICES = (
    Service(
        name="backend",
        command=(
            os.fspath(BACKEND_PYTHON),
            "-m",
            "omnicell_agent.api.cli",
        ),
        cwd=ROOT,
    ),
    Service(
        name="frontend",
        command=("npm", "run", "dev"),
        cwd=FRONTEND,
    ),
)


class BackendProbeState(str, Enum):
    NOT_RUNNING = "not_running"
    OMNICELL_RUNNING = "omnicell_running"
    PORT_OCCUPIED = "port_occupied"


def _backend_base_url() -> str:
    host = os.environ.get("OMNICELL_API_HOST", "127.0.0.1").strip()
    raw_port = os.environ.get("OMNICELL_API_PORT", "8000").strip()
    if not host:
        raise RuntimeError("OMNICELL_API_HOST 不能为空")
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise RuntimeError("OMNICELL_API_PORT 必须是整数") from exc
    if not 1 <= port <= 65_535:
        raise RuntimeError("OMNICELL_API_PORT 必须在 1..65535 之间")

    probe_host = {
        "0.0.0.0": "127.0.0.1",
        "::": "::1",
    }.get(host, host)
    url_host = f"[{probe_host}]" if ":" in probe_host else probe_host
    return f"http://{url_host}:{port}"


def _probe_backend(base_url: str) -> BackendProbeState:
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        raise RuntimeError(f"无法识别 backend 地址：{base_url}")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    try:
        with socket.create_connection(
            (parsed.hostname, port),
            timeout=BACKEND_PROBE_TIMEOUT_SECONDS,
        ):
            pass
    except OSError:
        return BackendProbeState.NOT_RUNNING

    liveness_url = f"{base_url.rstrip('/')}{BACKEND_LIVENESS_PATH}"
    request = Request(
        liveness_url,
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(  # noqa: S310 - 仅探测用户配置的本地开发服务
            request,
            timeout=BACKEND_PROBE_TIMEOUT_SECONDS,
        ) as response:
            body = response.read(4_097)
    except (OSError, TimeoutError, URLError):
        return BackendProbeState.PORT_OCCUPIED

    if len(body) > 4_096:
        return BackendProbeState.PORT_OCCUPIED
    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        return BackendProbeState.PORT_OCCUPIED
    if (
        isinstance(payload, dict)
        and payload.get("schema_version") == 1
        and payload.get("status") == "alive"
    ):
        return BackendProbeState.OMNICELL_RUNNING
    return BackendProbeState.PORT_OCCUPIED


def _backend_port(base_url: str) -> int:
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        raise RuntimeError(f"无法识别 backend 地址：{base_url}")
    return parsed.port or (443 if parsed.scheme == "https" else 80)


def _listener_pids(base_url: str) -> tuple[int, ...]:
    lsof = shutil.which("lsof")
    if lsof is None:
        raise RuntimeError("无法重启现有 backend：本机缺少 lsof")
    completed = subprocess.run(
        (
            lsof,
            "-nP",
            "-t",
            f"-iTCP:{_backend_port(base_url)}",
            "-sTCP:LISTEN",
        ),
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if completed.returncode not in {0, 1}:
        raise RuntimeError("无法定位现有 backend 的监听进程")
    pids: set[int] = set()
    for raw_pid in completed.stdout.splitlines():
        try:
            pid = int(raw_pid.strip())
        except ValueError as exc:
            raise RuntimeError("lsof 返回了无法识别的进程 ID") from exc
        if pid > 0:
            pids.add(pid)
    return tuple(sorted(pids))


def _process_command(pid: int) -> str:
    completed = subprocess.run(
        ("ps", "-p", str(pid), "-o", "command="),
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def _process_cwd(pid: int) -> Path | None:
    lsof = shutil.which("lsof")
    if lsof is None:
        return None
    completed = subprocess.run(
        (lsof, "-a", "-p", str(pid), "-d", "cwd", "-Fn"),
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if completed.returncode != 0:
        return None
    for line in completed.stdout.splitlines():
        if not line.startswith("n"):
            continue
        try:
            return Path(line[1:]).expanduser().resolve(strict=True)
        except (OSError, RuntimeError):
            return None
    return None


def _is_project_backend_process(pid: int) -> bool:
    cwd = _process_cwd(pid)
    if cwd == ROOT or (cwd is not None and ROOT in cwd.parents):
        return True
    command = _process_command(pid)
    if not any(marker in command for marker in BACKEND_COMMAND_MARKERS):
        return False
    return os.fspath(ROOT) in command


def _wait_for_backend_stop(base_url: str, *, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _probe_backend(base_url) is BackendProbeState.NOT_RUNNING:
            return True
        time.sleep(BACKEND_STOP_POLL_SECONDS)
    return _probe_backend(base_url) is BackendProbeState.NOT_RUNNING


def _stop_existing_backend(base_url: str) -> None:
    pids = _listener_pids(base_url)
    if not pids:
        raise RuntimeError(
            "已识别到 OmniCell API，但无法定位其监听进程；拒绝进行宽泛停止"
        )
    foreign_pids = [
        pid for pid in pids if not _is_project_backend_process(pid)
    ]
    if foreign_pids:
        raise RuntimeError(
            "已识别到 OmniCell API，但监听进程不属于当前仓库；"
            f"拒绝停止 PID：{', '.join(map(str, foreign_pids))}"
        )
    print(
        f"[dev] 停止现有 backend（PID：{', '.join(map(str, pids))}）",
        flush=True,
    )
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
        except PermissionError as exc:
            raise RuntimeError(f"无权停止 backend PID {pid}") from exc
    if _wait_for_backend_stop(
        base_url,
        timeout=SHUTDOWN_TIMEOUT_SECONDS,
    ):
        print("[dev] 现有 backend 已停止，准备重新启动", flush=True)
        return

    print("[dev] 现有 backend 未及时退出，执行强制停止", flush=True)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            continue
        except PermissionError as exc:
            raise RuntimeError(f"无权强制停止 backend PID {pid}") from exc
    if not _wait_for_backend_stop(base_url, timeout=5):
        raise RuntimeError(
            f"{base_url} 在停止 backend 后仍被占用；"
            "可能存在自动重启服务，请手工确认"
        )


def _services_to_start() -> tuple[Service, ...]:
    backend_url = _backend_base_url()
    probe_state = _probe_backend(backend_url)
    if probe_state is BackendProbeState.OMNICELL_RUNNING:
        _stop_existing_backend(backend_url)
    if probe_state is BackendProbeState.PORT_OCCUPIED:
        raise RuntimeError(
            f"{backend_url} 已被占用，但未识别为 OmniCell API；"
            "请释放端口或设置 OMNICELL_API_PORT"
        )
    return SERVICES


def _check_prerequisites(services: tuple[Service, ...]) -> None:
    required_executables = {
        service.command[0]
        for service in services
    }
    missing = sorted(
        executable
        for executable in required_executables
        if shutil.which(executable) is None
    )
    if missing:
        raise RuntimeError(f"缺少本地命令：{', '.join(missing)}")
    if (
        any(service.name == "frontend" for service in services)
        and not (FRONTEND / "node_modules").is_dir()
    ):
        raise RuntimeError("frontend 依赖尚未安装，请先在 frontend/ 执行 npm ci")
    backend = next(
        (service for service in services if service.name == "backend"),
        None,
    )
    if backend is not None:
        completed = subprocess.run(
            (
                backend.command[0],
                "-c",
                "import omnicell_agent.api.bootstrap",
            ),
            cwd=backend.cwd,
            env=_service_environment(backend),
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "当前 checkout 的 backend 无法导入；"
                "请先执行 uv sync --package omnicell-agent"
            )


def _service_environment(service: Service) -> dict[str, str]:
    environment = dict(os.environ)
    if service.name != "backend":
        return environment
    existing_pythonpath = environment.get("PYTHONPATH", "").strip()
    source = os.fspath(BACKEND_SOURCE)
    environment["PYTHONPATH"] = (
        source
        if not existing_pythonpath
        else source + os.pathsep + existing_pythonpath
    )
    return environment


def _start(service: Service) -> subprocess.Popen[bytes]:
    print(
        f"[dev] 启动 {service.name}: {' '.join(service.command)}",
        flush=True,
    )
    return subprocess.Popen(
        service.command,
        cwd=service.cwd,
        env=_service_environment(service),
        start_new_session=True,
    )


def _stop(service: Service, process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    print(f"[dev] 停止 {service.name}", flush=True)
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=SHUTDOWN_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except ProcessLookupError:
            return
        process.wait(timeout=5)


def main() -> int:
    _check_prerequisites(SERVICES)
    services = _services_to_start()
    shutdown = Event()
    received_signal: list[int] = []

    def request_shutdown(signum: int, _frame: object) -> None:
        received_signal.append(signum)
        shutdown.set()

    signal.signal(signal.SIGINT, request_shutdown)
    signal.signal(signal.SIGTERM, request_shutdown)

    processes: list[tuple[Service, subprocess.Popen[bytes]]] = []
    exit_code = 0
    try:
        for service in services:
            processes.append((service, _start(service)))
        backend_url = _backend_base_url()
        print(
            "[dev] Frontend: http://127.0.0.1:5173  "
            f"API: {backend_url}/api/v1/docs",
            flush=True,
        )
        print("[dev] 按 Ctrl+C 停止本次启动的服务", flush=True)

        while not shutdown.wait(0.2):
            for service, process in processes:
                return_code = process.poll()
                if return_code is None:
                    continue
                print(
                    f"[dev] {service.name} 已退出，code={return_code}",
                    flush=True,
                )
                exit_code = return_code if return_code != 0 else 1
                shutdown.set()
                break
        if received_signal:
            exit_code = (
                0
                if received_signal[0] == signal.SIGINT
                else 128 + received_signal[0]
            )
    finally:
        for service, process in reversed(processes):
            _stop(service, process)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
