import logging
import sys

from lit import config

logging.basicConfig(
    level=config.LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def _stop_background_tasks(app):
    """本地退出（Ctrl+C）时优雅停机：从 app.extensions["lit"] 取出任务管理器。"""
    lit_ext = app.extensions.get("lit") if app is not None else None
    if not lit_ext:
        return
    tasks = lit_ext[2]
    try:
        tasks.stop()
    except Exception:
        logger.exception("后台任务停止失败（退出继续）")


def main():
    logger.info("=== LIT 课表提醒服务启动 ===")
    from lit.web import create_app
    from lit.web.app import APP_PORT
    app = create_app()
    try:
        app.run(host="0.0.0.0", port=APP_PORT, debug=False)
    finally:
        _stop_background_tasks(app)


if __name__ == "__main__":
    main()
