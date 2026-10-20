import os

from .jwxt import JWXT_BASE_URL, JWXT_ACCOUNT, JWXT_PASSWORD, XNXQDM

# 通用配置：调度提醒提前量（原 schedule.py）
REMIND_BEFORE_MINUTES = int(os.getenv("REMIND_BEFORE_MINUTES", "30"))

COOKIE_REFRESH_INTERVAL = int(os.getenv("COOKIE_REFRESH_INTERVAL", "1800"))

# 通用配置：路径与日志（原 paths.py）
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE_DIR = os.getenv("CACHE_DIR", os.path.join(BASE_DIR, "cache"))
DATA_DIR = os.getenv("DATA_DIR", os.path.join(BASE_DIR, "data"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

__all__ = [
    "JWXT_BASE_URL", "JWXT_ACCOUNT", "JWXT_PASSWORD", "XNXQDM",
    "REMIND_BEFORE_MINUTES", "COOKIE_REFRESH_INTERVAL",
    "BASE_DIR", "CACHE_DIR", "DATA_DIR", "LOG_LEVEL",
]
