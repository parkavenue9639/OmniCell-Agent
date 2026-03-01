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
        logger.info(f"Submitting {len(code)} characters to Docker Sandbox...")
        result = sandbox.execute_code(code)
        
        # 强制清理：每轮执行后释放内存大对象，避免累积引发如 zero-size array 异常或显存崩溃。
        clean_up_code = "import gc; locals().pop('adata', None); globals().pop('adata', None); gc.collect();"
        sandbox.execute_code(clean_up_code)
        
        trace_logger.append_sandbox_execution(code=code, result=result)
        
        # 结果包装回传
        return {"sandbox_execution_result": result}
    except Exception as e:
        logger.error(f"Sandbox execution fatal error: {e}")
        return {"sandbox_execution_result": {"status": "error", "error": str(e)}}
