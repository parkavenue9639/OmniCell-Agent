
from pathlib import Path

class PromptManager:
    """
    提示词集中管理载体。
    负责从项目 `prompts` 目录下加载文本模版，并动态注入变量替换。
    """
    def __init__(self, prompts_dir: str = None):
        if prompts_dir is None:
            # 默认指向 src/omnicell_agent/prompts
            base_path = Path(__file__).parent.parent
            self.prompts_dir = base_path / "prompts"
        else:
            self.prompts_dir = Path(prompts_dir)

    def load_prompt(self, template_name: str, **kwargs) -> str:
        """
        加载指定名称的提示词模板文件，并将其中的变量替换为 kwargs 传入的值。
        
        用法:
            prompt = manager.load_prompt("planner_system.txt", var1="value1")
        
        如果模板内部有 Python 格式化的 `{}` 占位符，直接使用 `format` 方法。
        """
        file_path = self.prompts_dir / template_name
        
        if not file_path.exists():
            raise FileNotFoundError(f"Prompt template file not found: {file_path}")
            
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
            
        # 如果 kwargs 有传入参数，进行替换
        if kwargs:
            try:
                # 推荐在 txt/md 里依然使用 {var} 风格进行书写
                content = content.format(**kwargs)
            except KeyError as e:
                # 为了防止由于 prompt 内部碰巧出现 json 数据大括号 `{}` 导致的血案
                # 更稳健的做法是提示报错，或者建议在存放 json 示例的地方用双大括号 `{{}}` 进行转移
                raise ValueError(f"Prompt template '{template_name}' requires formatting variable: {e}")
                
        return content

# 导出全局单例管理中心，任意节点均可通过 import 引用
prompt_manager = PromptManager()
