import logging
import sys

def get_logger(name: str = "omnicell") -> logging.Logger:
    """
    获取全局统一的极简日志器。
    科研原型的日志注重终端(stdout)的易读性，排除例如 ELK 或者 Kafka 的重型业务流转策略。
    """
    logger = logging.getLogger(name)
    
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        # 用轻量化格式打印
        fmt = logging.Formatter(
            fmt="[%(asctime)s] %(levelname)s [%(name)s.%(funcName)s:%(lineno)d] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(fmt)
        logger.addHandler(console_handler)
        
    return logger

# 预实例化一个根 logger 供系统全局直接导入
logger = get_logger()
