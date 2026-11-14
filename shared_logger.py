"""共享日志模块：供 modian_tracker.py 和 sync_data.py 共同使用"""
import logging
import os
import sys
from pathlib import Path

# 日志文件名（相对于脚本所在目录）；测试/特殊环境可用 TRACKER_LOG_PATH 覆盖
LOG_FILE = "tracker_log.log"
SCRIPT_DIR = Path(__file__).resolve().parent
LOG_PATH = (Path(os.environ["TRACKER_LOG_PATH"]).resolve()
            if os.environ.get("TRACKER_LOG_PATH") else SCRIPT_DIR / LOG_FILE)

# 全局标记，防止重复初始化
_initialized = False


def setup_logging():
    """配置日志：同时输出到控制台与日志文件（幂等，多次调用无副作用）"""
    global _initialized
    if _initialized:
        return

    handlers = []
    try:
        handlers.append(logging.FileHandler(LOG_PATH, encoding="utf-8"))
    except OSError as exc:
        print(f"警告: 无法写入日志文件 {LOG_PATH}，仅输出到控制台: {exc}", file=sys.stderr)
    handlers.append(logging.StreamHandler())

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )
    _initialized = True


def get_logger(name=None):
    """获取 logger 实例，自动完成初始化"""
    setup_logging()
    return logging.getLogger(name)
