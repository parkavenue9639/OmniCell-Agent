import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from omnicell_agent.schema.state import DataPipeline_State
from omnicell_agent.runtime import LocalDockerPythonSession, register_runtime_cancel
from omnicell_agent.core.config import project_root

logger = logging.getLogger(__name__)

_python_session_context: ContextVar[LocalDockerPythonSession | None] = ContextVar(
    "omnicell_graph_a_python_session", default=None
)

_HOST_DATA_DIR = str(project_root / "data")


def _to_sandbox_path(path: str) -> str:
    """将宿主机绝对 / 相对路径转换为容器内 /app/data/ 路径。"""
    if not path or path == "/app/data" or path.startswith("/app/data/"):
        return path
    abs_path = os.path.abspath(path)
    if os.path.commonpath((abs_path, _HOST_DATA_DIR)) == _HOST_DATA_DIR:
        rel = os.path.relpath(abs_path, _HOST_DATA_DIR)
        return f"/app/data/{rel}"
    if path.startswith("data/"):
        return "/app/data/" + path[5:]
    return path


def get_python_session() -> LocalDockerPythonSession:
    session = _python_session_context.get()
    if session is None:
        raise RuntimeError(
            "Graph A executor 必须运行在显式 graph_a_python_session_scope 生命周期内"
        )
    return session


def _cleanup_python_session_with_retry(session: LocalDockerPythonSession) -> None:
    """对保留 owned identity 的瞬时回收失败做一次有界重试。"""

    first_failure: BaseException | None = None
    try:
        session.cleanup()
        return
    except BaseException as exc:
        first_failure = exc
        if getattr(session, "_closed", False):
            raise
    try:
        session.cleanup()
    except BaseException as retry_failure:
        assert first_failure is not None
        first_failure.add_note(
            "Graph A Python session cleanup retry failed with "
            f"{type(retry_failure).__name__}"
        )
        raise first_failure


@contextmanager
def graph_a_python_session_scope(
    session: LocalDockerPythonSession | None = None,
    *,
    host_workspace: str | Path | None = None,
) -> Iterator[LocalDockerPythonSession]:
    """为一次 Graph A 调用绑定并可靠回收独立 Python session。"""

    if session is None and host_workspace is None:
        raise ValueError(
            "graph_a_python_session_scope 必须提供 session 或 conversation host_workspace"
        )
    active = session or LocalDockerPythonSession(host_workspace=host_workspace)
    cancel_active = getattr(active, "cancel_active", None)
    cancel_callback = cancel_active if callable(cancel_active) else (lambda: False)
    with register_runtime_cancel(cancel_callback):
        try:
            active.start()
        except BaseException as exc:
            try:
                _cleanup_python_session_with_retry(active)
            except BaseException as cleanup_exc:
                exc.add_note(
                    "Python session startup cleanup failed: "
                    f"{type(cleanup_exc).__name__}"
                )
            raise
        token = _python_session_context.set(active)
        try:
            yield active
        finally:
            _python_session_context.reset(token)
            _cleanup_python_session_with_retry(active)

def run_executor(state: DataPipeline_State) -> dict:
    """
    Executor Node (Sandbox Node)
    提取 Programmer 刚刚生成的代码，将其推入 Docker Sandbox 环境执行。
    将环境的 stdout 和 stderr 的日志反馈以供下游 Evaluator 审查。
    """
    logger.info("--- NODE: EXECUTOR (SANDBOX) ---")
    code = state.get("last_generated_code", "")
    if not code:
        logger.warning("No code provided to execute, returning empty sandbox state.")
        return {"sandbox_execution_result": {"status": "error", "error": "No code provided from Programmer."}}

    session = get_python_session()

    try:
        raw_data_path = _to_sandbox_path(
            state.get("raw_data_path", "/app/data/pbmc3k_raw.h5ad")
        )
        marker_table_path = _to_sandbox_path(
            state.get("marker_table_path", "/app/data/markers.json")
        )
        artifact_output_root = marker_table_path.rsplit("/", 1)[0]
        inject_code = (
            f"raw_data_path = {raw_data_path!r}\n"
            f"marker_table_path = {marker_table_path!r}\n"
            f"artifact_output_root = {artifact_output_root!r}\n"
        )
        session.execute_code(inject_code)
        logger.info(
            "Injected sandbox globals: raw_data_path=%s, marker_table_path=%s, "
            "artifact_output_root=%s",
            raw_data_path, marker_table_path, artifact_output_root,
        )

        # 执行前：使用深拷贝生成沙盒内的上下文环境备份，以防止代码执行崩溃导致 adata 被半脏数据污染
        backup_code = "if 'adata' in locals() or 'adata' in globals(): adata_backup = adata.copy()"
        session.execute_code(backup_code)

        # 下放至 Sandbox 执行
        logger.info(f"Submitting {len(code)} characters to Docker Sandbox...")
        result = session.execute_code(code)

        if result.get("status") == "error":
            # 报错自毁恢复机制：如果报错，把之前的 adata_backup 再 copy 回去，抹除掉所有错误修改
            logger.warning("Sandbox 运行时触发报错，启动安全备份还原 adata...')")
            restore_code = "if 'adata_backup' in locals() or 'adata_backup' in globals(): adata = adata_backup.copy(); del adata_backup; import gc; gc.collect()"
            session.execute_code(restore_code)
        else:
            # 正常执行：丢弃备份件，释放巨大的单细胞内存
            cleanup_backup_code = "if 'adata_backup' in locals() or 'adata_backup' in globals(): del adata_backup; import gc; gc.collect()"
            session.execute_code(cleanup_backup_code)

        # 结果包装回传
        return {"sandbox_execution_result": result}
    except Exception as e:
        logger.error(f"Sandbox execution fatal error: {e}")
        return {"sandbox_execution_result": {"status": "error", "error": str(e)}}
