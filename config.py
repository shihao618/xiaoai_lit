import os

JWXT_BASE_URL = os.getenv("JWXT_BASE_URL", "https://lljw.lit.edu.cn")
JWXT_ACCOUNT = os.getenv("JWXT_ACCOUNT", "")
JWXT_PASSWORD = os.getenv("JWXT_PASSWORD", "")

XIAOAI_TTS_BACKUP_URL = os.getenv("XIAOAI_TTS_BACKUP_URL", "")
XIAOAI_TTS_BASE_URL = os.getenv("XIAOAI_TTS_BASE_URL", "")

COURSE_TIME_MAP = {
    1: "08:00", 2: "08:55", 3: "10:00", 4: "10:55",
    5: "14:30", 6: "15:25", 7: "16:30", 8: "17:25",
    9: "19:10", 10: "20:05",
}

REMIND_BEFORE_MINUTES = int(os.getenv("REMIND_BEFORE_MINUTES", "30"))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.getenv("CACHE_DIR", os.path.join(BASE_DIR, "cache"))
DATA_DIR = os.getenv("DATA_DIR", os.path.join(BASE_DIR, "data"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

XNXQDM = os.getenv("XNXQDM", "")

COOKIE_REFRESH_INTERVAL = int(os.getenv("COOKIE_REFRESH_INTERVAL", "3600"))