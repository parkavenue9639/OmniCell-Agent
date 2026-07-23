"""同时启动并可靠回收 OmniCell-Agent 本地前后端服务。"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path
from threading import Event


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
SHUTDOWN_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True, slots=True)
class Service:
    name: str
    command: tuple[str, ...]
    cwd: Path


SERVICES = (
    Service(
        name="backend",
        command=(
            "uv",
            "run",
            "--package",
            "omnicell-agent",
            "omnicell-api",
        ),
        cwd=ROOT,
    ),
    Service(
        name="frontend",
        command=("npm", "run", "dev"),
        cwd=FRONTEND,
    ),
)


def _check_prerequisites() -> None:
    missing = [
        executable
        for executable in ("uv", "npm")
        if shutil.which(executable) is None
    ]
    if missing:
        raise RuntimeError(f"缺少本地命令：{', '.join(missing)}")
    if not (FRONTEND / "node_modules").is_dir():
        raise RuntimeError("frontend 依赖尚未安装，请先在 frontend/ 执行 npm ci")


def _start(service: Service) -> subprocess.Popen[bytes]:
    print(
        f"[dev] 启动 {service.name}: {' '.join(service.command)}",
        flush=True,
    )
    return subprocess.Popen(
        service.command,
        cwd=service.cwd,
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
    _check_prerequisites()
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
        for service in SERVICES:
            processes.append((service, _start(service)))
        print(
            "[dev] Frontend: http://127.0.0.1:5173  "
            "API: http://127.0.0.1:8000/api/v1/docs",
            flush=True,
        )
        print("[dev] 按 Ctrl+C 同时停止前后端", flush=True)

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
