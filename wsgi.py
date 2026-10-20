"""WSGI 入口：容器内由 gunicorn 调用（单 worker + gthread）；本地开发请用 python main.py。"""
import logging
import sys

from lit import config

# gunicorn 不配置 root logger，这里把应用日志接到 stdout 并套用 LOG_LEVEL，
# 否则容器内 lit.* 的 INFO 日志会被 root logger 的默认 WARNING 级别吞掉。
logging.basicConfig(
    level=config.LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

from lit.web import create_app

app = create_app()
