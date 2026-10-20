"""应用工厂：装配业务对象、注册 4 蓝图、启动后台任务（D6，装配顺序冻结）。"""
import atexit
import logging
import os

from flask import Flask

from lit import config
from lit.jwxt import JWXTClient
from lit.tasks import BackgroundTasks
from lit.web.blueprints.jwxt import create_jwxt_blueprint
from lit.web.blueprints.pages import create_pages_blueprint
from lit.web.blueprints.status import create_status_blueprint
from lit.web.blueprints.xiaoai import create_xiaoai_blueprint
from lit.xiaoai import XiaoAISpeaker

logger = logging.getLogger(__name__)

APP_PORT = 10379

# 进程退出钩子去重：同一 BackgroundTasks 实例只注册一次（atexit 持有强引用）。
_atexit_registered = set()


def _stop_tasks_quietly(tasks):
    """atexit 停机：任何异常都不得影响进程退出码。"""
    try:
        tasks.stop()
    except Exception:
        logger.exception("atexit 停止后台任务失败（已忽略）")


def _register_atexit_shutdown(tasks):
    """为单个 BackgroundTasks 注册进程退出停机（幂等，可多次 create_app）。"""
    key = id(tasks)
    if key in _atexit_registered:
        return
    _atexit_registered.add(key)
    atexit.register(_stop_tasks_quietly, tasks)


def create_app():
    os.makedirs(config.DATA_DIR, exist_ok=True)
    os.makedirs(config.CACHE_DIR, exist_ok=True)
    jwxt_client = JWXTClient()
    xiaoai_speaker = XiaoAISpeaker()
    tasks = BackgroundTasks(jwxt_client, xiaoai_speaker)
    app = Flask(__name__, template_folder="templates", static_folder=None)
    app.register_blueprint(create_pages_blueprint())
    app.register_blueprint(create_jwxt_blueprint(jwxt_client))
    app.register_blueprint(create_xiaoai_blueprint(xiaoai_speaker))
    app.register_blueprint(create_status_blueprint(jwxt_client, xiaoai_speaker))
    xiaoai_speaker.restore_on_start()
    try:
        tasks.start()
    except Exception as e:
        logger.error(f"后台任务启动失败: {e}")
    app.extensions["lit"] = (jwxt_client, xiaoai_speaker, tasks)
    _register_atexit_shutdown(tasks)
    return app
