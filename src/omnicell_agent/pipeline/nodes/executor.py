import logging
from omnicell_agent.schema.state import DataPipeline_State
from omnicell_agent.sandbox.docker_manager import DockerJupyterSandbox
from omnicell_agent.core.trace_logger import trace_logger

logger = logging.getLogger(__name__)

# 全局或者生命周期内维持单例 Sandbox
_sandbox_instance = None

def get_sandbox() -> DockerJupyterSandbox:
    global _sandbox_instance
    if _sandbox_instance is None:
        logger.info("Initializing Docker Jupyter Sandbox for Executor Node...")
        _sandbox_instance = DockerJupyterSandbox()
        # 激活生命周期以打通 Kernel Socket 连接
        _sandbox_instance.start()
    return _sandbox_instance

def run_executor(state: DataPipeline_State) -> dict:
    """
    Executor Node (Sandbox Node)
    提取 Programmer 刚刚生成的代码，将其推入 Docker Sandbox 环境执行。
    将环境的 stdout 和 stderr 的日志反馈以供下游 Evaluator 审查。
    """
    logger.info("--- NODE: EXECUTOR (SANDBOX) ---")
    trace_logger.append_node_start("EXECUTOR_SANDBOX")
    
    code = state.get("last_generated_code", "")
    if not code:
        logger.warning("No code provided to execute, returning empty sandbox state.")
        return {"sandbox_execution_result": {"status": "error", "error": "No code provided from Programmer."}}
        
    sandbox = get_sandbox()
    
    try:
        # 下放至 Sandbox 执行
        # 执行前：使用深拷贝生成沙盒内的上下文环境备份，以防止代码执行崩溃导致 adata 被半脏数据污染
        backup_code = "if 'adata' in locals() or 'adata' in globals(): adata_backup = adata.copy()"
        sandbox.execute_code(backup_code)
        
        # 下放至 Sandbox 执行
        logger.info(f"Submitting {len(code)} characters to Docker Sandbox...")
        result = sandbox.execute_code(code)
        
        if result.get("status") == "error":
            # 报错自毁恢复机制：如果报错，把之前的 adata_backup 再 copy 回去，抹除掉所有错误修改
            logger.warning("Sandbox 运行时触发报错，启动安全备份还原 adata...')")
            restore_code = "if 'adata_backup' in locals() or 'adata_backup' in globals(): adata = adata_backup.copy(); del adata_backup; import gc; gc.collect()"
            sandbox.execute_code(restore_code)
        else:
            # 正常执行：丢弃备份件，释放巨大的单细胞内存
            cleanup_backup_code = "if 'adata_backup' in locals() or 'adata_backup' in globals(): del adata_backup; import gc; gc.collect()"
            sandbox.execute_code(cleanup_backup_code)
        
        trace_logger.append_sandbox_execution(code=code, result=result)
        
        # 结果包装回传
        return {"sandbox_execution_result": result}
    except Exception as e:
        logger.error(f"Sandbox execution fatal error: {e}")
        return {"sandbox_execution_result": {"status": "error", "error": str(e)}}
