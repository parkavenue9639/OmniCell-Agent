import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path, PurePosixPath
from omnicell_agent.schema.state import ExploratoryAnalysisState
from omnicell_agent.runtime import LocalDockerPythonSession, register_runtime_cancel
from omnicell_agent.core.config import project_root
from omnicell_agent.recipes.catalog import (
    RecipeCatalogError,
    load_builtin_recipe_catalog,
)

logger = logging.getLogger(__name__)

_python_session_context: ContextVar[LocalDockerPythonSession | None] = ContextVar(
    "omnicell_analysis_python_session", default=None
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


def _require_sandbox_file_path(
    state: ExploratoryAnalysisState,
    field_name: str,
) -> str:
    """Resolve one explicit invocation path and reject absent/escaping state."""

    value = state.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"探索性分析缺少权威 {field_name}，未执行 sandbox 代码"
        )
    sandbox_path = _to_sandbox_path(value.strip())
    parsed = PurePosixPath(sandbox_path)
    if (
        not sandbox_path.startswith("/app/data/")
        or ".." in parsed.parts
        or parsed.name in {"", ".", ".."}
    ):
        raise ValueError(
            f"探索性分析 {field_name} 不在当前 sandbox data 边界内"
        )
    return sandbox_path


def get_python_session() -> LocalDockerPythonSession:
    session = _python_session_context.get()
    if session is None:
        raise RuntimeError(
            "分析执行器必须运行在显式 analysis_python_session_scope 生命周期内"
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
            "Analysis Python session cleanup retry failed with "
            f"{type(retry_failure).__name__}"
        )
        raise first_failure


@contextmanager
def analysis_python_session_scope(
    session: LocalDockerPythonSession | None = None,
    *,
    host_workspace: str | Path | None = None,
) -> Iterator[LocalDockerPythonSession]:
    """为一次科学分析调用绑定并可靠回收独立 Python session。"""

    if session is None and host_workspace is None:
        raise ValueError(
            "analysis_python_session_scope 必须提供 session 或 conversation host_workspace"
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

def run_executor(state: ExploratoryAnalysisState) -> dict:
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
        raw_data_path = _require_sandbox_file_path(state, "raw_data_path")
        marker_table_path = _require_sandbox_file_path(
            state,
            "marker_table_path",
        )
        base_output_root = marker_table_path.rsplit("/", 1)[0]
        plan_steps = state.get("plan_steps", [])
        current_index = int(state.get("current_step_index", 0) or 0)
        retry_count = int(
            (state.get("task_context") or {}).get("retry_count", 0) or 0
        )
        artifact_output_root = (
            f"{base_output_root}/attempt-{current_index:02d}-{retry_count:02d}"
        )
        marker_table_path = f"{artifact_output_root}/markers.json"
        tool_parameters: dict[str, object] = {}
        if current_index < len(plan_steps):
            current_step = plan_steps[current_index]
            if current_step.get("step_type") == "recipe_call":
                try:
                    tool_parameters = load_builtin_recipe_catalog().get(
                        str(current_step.get("recipe_name") or "")
                    ).normalize_parameters(
                        dict(current_step.get("parameters") or {})
                    )
                except RecipeCatalogError as exc:
                    return {
                        "sandbox_execution_result": {
                            "status": "error",
                            "error": (
                                "Recipe parameters failed validation before "
                                "sandbox execution."
                            ),
                        }
                    }
        inject_code = (
            f"raw_data_path = {raw_data_path!r}\n"
            f"marker_table_path = {marker_table_path!r}\n"
            f"artifact_output_root = {artifact_output_root!r}\n"
            f"tool_parameters = {tool_parameters!r}\n"
        )
        session.execute_code(inject_code)
        logger.info(
            "Injected sandbox globals: raw_data_path=%s, marker_table_path=%s, "
            "artifact_output_root=%s, tool_parameter_keys=%s",
            raw_data_path,
            marker_table_path,
            artifact_output_root,
            sorted(tool_parameters),
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
        return {
            "sandbox_execution_result": {
                **result,
                "attempt_output_root": artifact_output_root,
                "attempt_marker_table_path": marker_table_path,
            }
        }
    except Exception as e:
        logger.error(f"Sandbox execution fatal error: {e}")
        return {"sandbox_execution_result": {"status": "error", "error": str(e)}}
