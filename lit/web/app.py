"""应用工厂：装配业务对象、注册 5 蓝图、启动后台任务（D6，装配顺序冻结）。"""
import logging
import os

from flask import Flask

from lit import config
from lit.jwxt import JWXTClient
from lit.tasks import BackgroundTasks
from lit.web.blueprints.audio import create_audio_blueprint
from lit.web.blueprints.jwxt import create_jwxt_blueprint
from lit.web.blueprints.pages import create_pages_blueprint
from lit.web.blueprints.status import create_status_blueprint
from lit.web.blueprints.xiaoai import create_xiaoai_blueprint
from lit.xiaoai import XiaoAISpeaker

logger = logging.getLogger(__name__)


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
    app.register_blueprint(create_audio_blueprint())
    app.register_blueprint(create_status_blueprint(jwxt_client, xiaoai_speaker))
    xiaoai_speaker.restore_on_start()
    try:
        tasks.start()
    except Exception as e:
        logger.error(f"后台任务启动失败: {e}")
    app.extensions["lit"] = (jwxt_client, xiaoai_speaker, tasks)
    return app