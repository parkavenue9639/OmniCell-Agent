from langchain_openai import ChatOpenAI
from langchain_core.language_models.chat_models import BaseChatModel
from omnicell_agent.core.config import (
    OPENROUTER_API_KEY, 
    OPENROUTER_BASE_URL, 
    DEFAULT_OPENROUTER_MODEL,
    APP_REFERER,
    APP_TITLE,
    ONEROUTER_API_KEY,
    ONEROUTER_BASE_URL,
    DEFAULT_ONEROUTER_MODEL
)
import logging

logger = logging.getLogger(__name__)

class LLMSelector:
    """
    通过汲取基建代码中的 LLM 工厂思路实现的轻量级全能大模型选择器。
    专门强化了对 OpenRouter 的支持，便于在各种 SOTA 模型中无缝热切换。
    """
    
    @staticmethod
    def get_llm(model_name: str = "openrouter:default", temperature: float = 0.0, max_retries: int = 3) -> BaseChatModel:
        """
        根据名称工厂化提取大模型实例。
        例如：
        - `openrouter:anthropic/claude-3-sonnet` 会调用 OpenRouter 的 Claude
        - `openrouter:default` 会使用 .env 中的 DEFAULT_OPENROUTER_MODEL
        - `openai:gpt-4o` 会使用标准的直连 OpenAI 渠道
        """
        
        # 1. 尝试解析是否为 OpenRouter 路由请求
        if model_name.startswith("openrouter:") or model_name == "openrouter":
            if not OPENROUTER_API_KEY:
                raise ValueError("OPENROUTER_API_KEY 未设置，请检查 .env。当前平台切换均极度依赖它。")
            
            # 解析实际指定的模型名称
            actual_model = model_name.replace("openrouter:", "")
            if actual_model == "default" or actual_model == "openrouter":
                actual_model = DEFAULT_OPENROUTER_MODEL
                
            logger.info(f"实例化 OpenRouter 模型代理: [{actual_model}]")
            
            # 组装 OpenRouter 特有 Header 
            default_headers = {
                "HTTP-Referer": APP_REFERER,
                "X-Title": APP_TITLE
            }
            
            return LLMSelector._create_openai_compatible_llm(
                model=actual_model,
                base_url=OPENROUTER_BASE_URL,
                api_key=OPENROUTER_API_KEY,
                temperature=temperature,
                max_retries=max_retries,
                default_headers=default_headers
            )
            
        # 2. 传统直连 OpenAI 或其衍生兼容平台 (在此特指您提供的 OneRouter 中转)
        elif model_name.startswith("onerouter:") or model_name == "onerouter":
            if not ONEROUTER_API_KEY:
                raise ValueError("ONEROUTER_API_KEY 未设置!")
                
            actual_model = model_name.replace("onerouter:", "")
            if actual_model == "default" or actual_model == "onerouter":
                actual_model = DEFAULT_ONEROUTER_MODEL
                
            logger.info(f"实例化的 OneRouter 中转大模型: [{actual_model}]")
            
            return LLMSelector._create_openai_compatible_llm(
                model=actual_model,
                base_url=ONEROUTER_BASE_URL,
                api_key=ONEROUTER_API_KEY,
                temperature=temperature,
                max_retries=max_retries
            )
            
        else:
            raise ValueError(f"未知的模型提供商路由前缀: {model_name}。请使用 openrouter:... 或者 onerouter:...")

    @staticmethod
    def _create_openai_compatible_llm(model: str, base_url: str, api_key: str, temperature: float, max_retries: int, default_headers: dict = None) -> BaseChatModel:
        """底层全数收口于兼容度最高的 ChatOpenAI 实例建立，并开启 langChain 自带的自动防宕机重试"""
        llm_kwargs = {
            "model": model,
            "temperature": temperature,
            "api_key": api_key,
            "base_url": base_url,
            "max_retries": max_retries,
            "request_timeout": 120
        }
        
        if default_headers:
            llm_kwargs["default_headers"] = default_headers
            
        try:
            return ChatOpenAI(**llm_kwargs)
        except Exception as e:
            logger.error(f"无法初始化兼容的大模型客户端，异常: {e}")
            raise
