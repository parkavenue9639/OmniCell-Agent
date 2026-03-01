import os
import datetime

class TraceLogger:
    """
    全链路智能体执行轨迹持久化拦截器。
    用于以结构化 Markdown 文件 (如 TRACE_RUN_YYYYMMDD_HHMMSS.md) 记录每一次 LLM 消耗与沙盒验证的交互详情。
    该类为单例，以保证同一生命周期的流转追加至同一文件。
    """
    _instance = None
    _log_file_path = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(TraceLogger, cls).__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        # 初始化创建独立的 Markdown 日志文件
        log_dir = os.path.join(os.path.dirname(__file__), "..", "..", "logs", "runs")
        os.makedirs(log_dir, exist_ok=True)
        
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self._log_file_path = os.path.join(log_dir, f"TRACE_RUN_{timestamp}.md")
        
        # 写入头标
        with open(self._log_file_path, "w", encoding="utf-8") as f:
            f.write("# 🧬 OmniCell-Agent Execution Trace\n")
            f.write(f"**Run Timestamp:** `{timestamp}`\n")
            f.write("---\n\n")

    def get_log_path(self) -> str:
        return self._log_file_path

    def append_node_start(self, node_name: str):
        with open(self._log_file_path, "a", encoding="utf-8") as f:
            f.write(f"## 🟢 [{node_name.upper()}] Node Initiated\n")
            f.write(f"> Timestamp: {datetime.datetime.now().strftime('%H:%M:%S')}\n\n")

    def append_llm_interaction(self, system_prompt: str, human_prompt: str, llm_response: str, role_name: str = "LLM"):
        with open(self._log_file_path, "a", encoding="utf-8") as f:
            f.write(f"### 💬 {role_name} Interaction\n")
            
            f.write("#### 📥 System Prompt:\n")
            f.write(f"```text\n{system_prompt}\n```\n\n")
            
            f.write("#### 📩 Human Message (Task/Context):\n")
            f.write(f"```text\n{human_prompt}\n```\n\n")
            
            f.write("#### 📤 LLM Response:\n")
            # Usually response is markdown, using raw wrapper
            f.write(f"{llm_response}\n\n---\n\n")

    def append_sandbox_execution(self, code: str, result: dict):
        with open(self._log_file_path, "a", encoding="utf-8") as f:
            f.write("### ⚙️ Sandbox Execution\n")
            f.write("#### 🐍 Attempted Code:\n")
            f.write(f"```python\n{code}\n```\n\n")
            
            f.write("#### 📊 Execution Result:\n")
            status = result.get('status', 'unknown')
            f.write(f"- **Status**: `{status}`\n")
            
            if status == "ok":
                f.write(f"- **Stdout**: \n```text\n{result.get('stdout', '')}\n```\n")
            else:
                f.write(f"**🔥 Error Traceback**:\n```text\n{result.get('stderr', '')}\n```\n")
            
            f.write("\n---\n\n")
            
    def append_vision_evaluation(self, image_path: str, result_dict: dict):
        with open(self._log_file_path, "a", encoding="utf-8") as f:
            f.write("### 👀 Vision Evaluator Assessment\n")
            f.write(f"- **Sniffed Target Image**: `{image_path}`\n")
            
            status = result_dict.get('status', 'unknown')
            pass_mark = "✅ PASS" if status == "pass" else "❌ REJECTED"
            f.write(f"- **Judgment**: {pass_mark}\n\n")
            
            f.write("#### 📝 Feedback Detail:\n")
            f.write(f"> {result_dict.get('feedback', '')}\n\n---\n\n")

    def append_pipeline_end(self, final_status: str, max_retries_hit: bool = False):
        with open(self._log_file_path, "a", encoding="utf-8") as f:
            f.write("## 🏁 Pipeline Execution Concluded\n")
            if max_retries_hit:
                f.write("**🚨 TRIGGERED ABORT:** Hit maximum retries limit.\n")
            f.write(f"**Final Status:** `{final_status}`\n")
            f.write(f"> Timestamp: {datetime.datetime.now().strftime('%H:%M:%S')}\n\n")

trace_logger = TraceLogger()
