"""编排并清理 React -> FastAPI -> PostgreSQL -> SSE live E2E。"""

from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
BACKEND_SERVER = ROOT / "backend" / "tests" / "e2e" / "live_server.py"
PLAYWRIGHT = FRONTEND / "node_modules" / ".bin" / "playwright"


def _port(name: str, fallback: int) -> int:
    value = int(os.environ.get(name, str(fallback)))
    if not 1 <= value <= 65_535:
        raise ValueError(f"{name} 超出合法端口范围")
    return value


def _assert_port_available(port: int) -> None:
    with socket.socket() as listener:
        try:
            listener.bind(("127.0.0.1", port))
        except OSError as exc:
            raise RuntimeError(f"live E2E 端口 {port} 已被占用") from exc


def _wait_url(url: str, process: subprocess.Popen[bytes], *, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"服务在 ready 前退出：{url}，exit={process.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if 200 <= response.status < 500:
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.1)
    raise TimeoutError(f"等待 live E2E 服务超时：{url}")


def _stop_process_group(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        group = os.getpgid(process.pid)
        os.killpg(group, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(group, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


def _drop_and_verify_schemas(dsn: str, schemas: tuple[str, str]) -> None:
    with psycopg.connect(dsn, autocommit=True) as connection:
        for schema_name in schemas:
            connection.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    sql.Identifier(schema_name)
                )
            )
        remaining = connection.execute(
            "SELECT schema_name FROM information_schema.schemata "
            "WHERE schema_name = ANY(%s)",
            (list(schemas),),
        ).fetchall()
    if remaining:
        raise RuntimeError(f"live E2E schema 清理失败：{remaining}")


def main() -> int:
    dsn = os.environ.get("OMNICELL_TEST_POSTGRES_DSN", "").strip()
    if not dsn:
        raise RuntimeError("运行 live E2E 前必须设置 OMNICELL_TEST_POSTGRES_DSN")
    api_port = _port("OMNICELL_LIVE_API_PORT", 18_080)
    web_port = _port("OMNICELL_LIVE_WEB_PORT", 14_173)
    _assert_port_available(api_port)
    _assert_port_available(web_port)
    if not PLAYWRIGHT.is_file():
        raise RuntimeError("frontend 依赖未安装，缺少 Playwright executable")
    npm = shutil.which("npm")
    if npm is None:
        raise RuntimeError("PATH 中找不到 npm")

    suffix = uuid4().hex[:12]
    schemas = (
        f"omnicell_live_app_{suffix}",
        f"omnicell_live_checkpoint_{suffix}",
    )
    backend: subprocess.Popen[bytes] | None = None
    frontend: subprocess.Popen[bytes] | None = None
    result = 1
    with tempfile.TemporaryDirectory(prefix="omnicell-live-e2e-") as workspace:
        environment = {
            **os.environ,
            "OMNICELL_TEST_POSTGRES_DSN": dsn,
            "OMNICELL_LIVE_API_PORT": str(api_port),
            "OMNICELL_LIVE_WEB_PORT": str(web_port),
            "OMNICELL_LIVE_APP_SCHEMA": schemas[0],
            "OMNICELL_LIVE_CHECKPOINT_SCHEMA": schemas[1],
            "OMNICELL_LIVE_WORKSPACE": workspace,
        }
        try:
            backend = subprocess.Popen(
                [sys.executable, os.fspath(BACKEND_SERVER)],
                cwd=ROOT,
                env=environment,
                start_new_session=True,
            )
            frontend = subprocess.Popen(
                [
                    npm,
                    "run",
                    "dev",
                    "--",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(web_port),
                    "--strictPort",
                ],
                cwd=FRONTEND,
                env={
                    **environment,
                    "OMNICELL_API_PROXY_TARGET": f"http://127.0.0.1:{api_port}",
                },
                start_new_session=True,
            )
            _wait_url(
                f"http://127.0.0.1:{api_port}/api/v1/openapi.json",
                backend,
                timeout=120,
            )
            _wait_url(
                f"http://127.0.0.1:{web_port}",
                frontend,
                timeout=120,
            )
            completed = subprocess.run(
                [os.fspath(PLAYWRIGHT), "test", "--config", "playwright.live.config.ts"],
                cwd=FRONTEND,
                env=environment,
                check=False,
            )
            result = completed.returncode
        finally:
            cleanup_errors: list[Exception] = []
            for process in (frontend, backend):
                try:
                    _stop_process_group(process)
                except Exception as exc:
                    cleanup_errors.append(exc)
            try:
                _drop_and_verify_schemas(dsn, schemas)
            except Exception as exc:
                cleanup_errors.append(exc)
            if cleanup_errors:
                raise ExceptionGroup("live E2E 资源清理失败", cleanup_errors)
            print(
                "LIVE_E2E_ORCHESTRATOR_CLEANED "
                f"app_schema={schemas[0]} checkpoint_schema={schemas[1]}",
                flush=True,
            )
    return result


if __name__ == "__main__":
    raise SystemExit(main())
