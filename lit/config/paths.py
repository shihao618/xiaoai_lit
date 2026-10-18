import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE_DIR = os.getenv("CACHE_DIR", os.path.join(BASE_DIR, "cache"))
DATA_DIR = os.getenv("DATA_DIR", os.path.join(BASE_DIR, "data"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")