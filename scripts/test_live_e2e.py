"""编排并清理 React -> FastAPI -> PostgreSQL -> SSE live E2E。"""

from __future__ import annotations

import argparse
import json
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
import anndata as ad
import numpy as np
import pandas as pd
from psycopg import sql


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
BACKEND_SERVER = ROOT / "backend" / "tests" / "e2e" / "live_server.py"
PLAYWRIGHT = FRONTEND / "node_modules" / ".bin" / "playwright"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--inspect",
        action="store_true",
        help=(
            "测试通过后保持 frontend/backend 运行，并保留 PostgreSQL schema "
            "与 workspace，便于在浏览器中检查真实执行记录。"
        ),
    )
    return parser.parse_args()


def _port(name: str, fallback: int) -> int:
    value = int(os.environ.get(name, str(fallback)))
    if not 1 <= value <= 65_535:
        raise ValueError(f"{name} 超出合法端口范围")
    return value


def _assert_port_available(port: int) -> None:
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
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


def _write_scientific_fixture(path: Path) -> None:
    ad.settings.allow_write_nullable_strings = True
    rng = np.random.default_rng(17)
    matrix = rng.poisson(0.3, size=(30, 120)).astype(np.float32)
    dataset = ad.AnnData(
        matrix,
        obs=pd.DataFrame(index=[f"cell-{index}" for index in range(30)]),
        var=pd.DataFrame(index=[f"GENE{index}" for index in range(120)]),
    )
    dataset.write_h5ad(path)


def _write_inspection_receipt(
    workspace: Path,
    *,
    api_port: int,
    web_port: int,
    schemas: tuple[str, str],
) -> Path:
    receipt = workspace / "inspection.json"
    receipt.write_text(
        json.dumps(
            {
                "frontend_url": f"http://127.0.0.1:{web_port}",
                "api_docs_url": f"http://127.0.0.1:{api_port}/api/v1/docs",
                "app_schema": schemas[0],
                "checkpoint_schema": schemas[1],
                "workspace": os.fspath(workspace),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return receipt


def _wait_for_inspection(
    *,
    api_port: int,
    web_port: int,
    schemas: tuple[str, str],
    workspace: Path,
    receipt: Path,
) -> None:
    print(
        "LIVE_E2E_INSPECT_READY "
        f"frontend=http://127.0.0.1:{web_port} "
        f"api=http://127.0.0.1:{api_port}/api/v1/docs "
        f"app_schema={schemas[0]} checkpoint_schema={schemas[1]} "
        f"workspace={workspace} receipt={receipt}",
        flush=True,
    )
    print(
        "live E2E 已完成；服务保持运行，可打开 frontend 查看真实记录。"
        "按 Ctrl+C 停止服务，schema 与 workspace 将继续保留。",
        flush=True,
    )
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        print("收到 Ctrl+C，正在停止 inspect 服务并保留数据。", flush=True)


def main() -> int:
    args = _parse_args()
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
    temporary_workspace: tempfile.TemporaryDirectory[str] | None = None
    if args.inspect:
        workspace = ROOT / "outputs" / "live-e2e-inspect" / suffix
        workspace.mkdir(parents=True, exist_ok=False)
    else:
        temporary_workspace = tempfile.TemporaryDirectory(
            prefix="omnicell-live-e2e-"
        )
        workspace = Path(temporary_workspace.name)
    preserve_data = False
    try:
        science_fixture = workspace / "science-fixture.h5ad"
        _write_scientific_fixture(science_fixture)
        environment = {
            **os.environ,
            "OMNICELL_TEST_POSTGRES_DSN": dsn,
            "OMNICELL_LIVE_API_PORT": str(api_port),
            "OMNICELL_LIVE_WEB_PORT": str(web_port),
            "OMNICELL_LIVE_APP_SCHEMA": schemas[0],
            "OMNICELL_LIVE_CHECKPOINT_SCHEMA": schemas[1],
            "OMNICELL_LIVE_WORKSPACE": os.fspath(workspace),
            "OMNICELL_LIVE_SCIENCE_FIXTURE": os.fspath(science_fixture),
            "OMNICELL_LIVE_E2E_PRESERVE_DATA": "1" if args.inspect else "0",
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
            if args.inspect and result == 0:
                receipt = _write_inspection_receipt(
                    workspace,
                    api_port=api_port,
                    web_port=web_port,
                    schemas=schemas,
                )
                preserve_data = True
                _wait_for_inspection(
                    api_port=api_port,
                    web_port=web_port,
                    schemas=schemas,
                    workspace=workspace,
                    receipt=receipt,
                )
        finally:
            cleanup_errors: list[Exception] = []
            for process in (frontend, backend):
                try:
                    _stop_process_group(process)
                except Exception as exc:
                    cleanup_errors.append(exc)
            if preserve_data:
                print(
                    "LIVE_E2E_INSPECT_PRESERVED "
                    f"app_schema={schemas[0]} "
                    f"checkpoint_schema={schemas[1]} workspace={workspace}",
                    flush=True,
                )
            else:
                try:
                    _drop_and_verify_schemas(dsn, schemas)
                except Exception as exc:
                    cleanup_errors.append(exc)
                if args.inspect:
                    shutil.rmtree(workspace, ignore_errors=True)
            if cleanup_errors:
                raise ExceptionGroup("live E2E 资源清理失败", cleanup_errors)
            if not preserve_data:
                print(
                    "LIVE_E2E_ORCHESTRATOR_CLEANED "
                    f"app_schema={schemas[0]} checkpoint_schema={schemas[1]}",
                    flush=True,
                )
    finally:
        if temporary_workspace is not None:
            temporary_workspace.cleanup()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
