"""共享日志模块：供 modian_tracker.py 和 sync_data.py 共同使用"""
import logging
from pathlib import Path

# 日志文件名（相对于脚本所在目录）
LOG_FILE = "tracker_log.log"
SCRIPT_DIR = Path(__file__).resolve().parent
LOG_PATH = SCRIPT_DIR / LOG_FILE

# 全局标记，防止重复初始化
_initialized = False


def setup_logging():
    """配置日志：同时输出到控制台与日志文件（幂等，多次调用无副作用）"""
    global _initialized
    if _initialized:
        return

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(LOG_PATH, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    _initialized = True


def get_logger(name=None):
    """获取 logger 实例，自动完成初始化"""
    setup_logging()
    return logging.getLogger(name)
