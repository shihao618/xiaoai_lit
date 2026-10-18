import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from xiaoai import run_async

logger = logging.getLogger(__name__)


class CookieManager:
    def __init__(self, jwxt_client, xiaoai_speaker):
        self.jwxt = jwxt_client
        self.xiaoai = xiaoai_speaker
        self.scheduler = BackgroundScheduler()

    def check_jwxt_cookie(self):
        logger.info("检查教务系统Cookie...")
        valid = self.jwxt.check_cookie_valid()
        if not valid:
            locked, lock_time = self.jwxt.is_locked()
            if locked:
                logger.warning(f"教务系统账号锁定/冷却中，解锁时间: {lock_time.strftime('%Y-%m-%d %H:%M:%S')}")
            else:
                logger.warning("教务系统Cookie失效，请在配置页面重新登录")
        return valid

    def check_xiaoai_cookie(self):
        logger.info("检查小爱音箱Cookie...")
        try:
            valid = run_async(self.xiaoai.check_cookie_valid())
            if not valid:
                logger.warning("小爱音箱Cookie失效，请在配置页面重新登录")
            return valid
        except Exception as e:
            logger.error(f"小爱Cookie检查异常: {e}")
            return False

    def refresh_all(self):
        self.check_jwxt_cookie()
        self.check_xiaoai_cookie()

    def start(self):
        self.scheduler.add_job(
            self.refresh_all,
            CronTrigger(minute="*/30"),
            id="cookie_refresh",
            replace_existing=True,
        )
        self.scheduler.start()
        logger.info("Cookie管理器已启动，每30分钟检查一次")
        try:
            self.check_xiaoai_cookie()
        except Exception as e:
            logger.error(f"启动时小爱Cookie检查失败: {e}")

    def stop(self):
        self.scheduler.shutdown()
        logger.info("Cookie管理器已停止")