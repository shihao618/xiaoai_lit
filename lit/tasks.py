"""统一后台任务模块：课表刷新、课程提醒、双系统 Cookie 检查（单调度器，D5/D4）。

合并原 scheduler.CourseScheduler 与 cookie_manager.CookieManager，
单一 BackgroundScheduler 承载全部 job，触发时刻与启动时序零变化。
"""
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.base import SchedulerNotRunningError
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from lit import config

logger = logging.getLogger(__name__)


class BackgroundTasks:
    def __init__(self, jwxt_client, xiaoai_speaker):
        self.scheduler = BackgroundScheduler()
        self.jwxt = jwxt_client
        self.speaker = xiaoai_speaker
        self.today_courses = []

    def refresh_course_table(self):
        logger.info("刷新课表数据...")
        courses = self.jwxt.get_course_table()
        if courses is not None:
            self.today_courses = self.jwxt.get_today_courses()
            logger.info(f"今日课程: {len(self.today_courses)} 门")
            for c in self.today_courses:
                logger.info(f"  {c.get('name')} 第{c.get('section_start')}-{c.get('section_end')}节 {c.get('start_time')} 教室:{c.get('classroom')}")
            self._schedule_reminders()
        else:
            logger.error("课表刷新失败")

    def _schedule_reminders(self):
        for job in list(self.scheduler.get_jobs()):
            if job.id.startswith("remind_"):
                self.scheduler.remove_job(job.id)

        morning = [c for c in self.today_courses if c.get("start_time", "99:99") < "12:00:00"]
        afternoon = [c for c in self.today_courses if c.get("start_time", "") >= "12:00:00"]

        if morning:
            earliest = min(c.get("start_time", "00:00:00") for c in morning)
            h, m = map(int, earliest.split(":")[:2])
            total = max(h * 60 + m - config.REMIND_BEFORE_MINUTES, 0)
            rh, rm = total // 60, total % 60
            self.scheduler.add_job(self._remind_morning, CronTrigger(hour=rh, minute=rm), id="remind_morning", replace_existing=True)
            logger.info(f"已设置上午提醒: {rh:02d}:{rm:02d} (最早课程 {earliest[:5]})")

        if afternoon:
            earliest = min(c.get("start_time", "23:59:59") for c in afternoon)
            h, m = map(int, earliest.split(":")[:2])
            total = max(h * 60 + m - config.REMIND_BEFORE_MINUTES, 0)
            rh, rm = total // 60, total % 60
            self.scheduler.add_job(self._remind_afternoon, CronTrigger(hour=rh, minute=rm), id="remind_afternoon", replace_existing=True)
            logger.info(f"已设置下午提醒: {rh:02d}:{rm:02d} (最早课程 {earliest[:5]})")

    def _remind_morning(self):
        morning = [c for c in self.today_courses if c.get("start_time", "99:99") < "12:00:00"]
        self.speaker.remind_morning(morning)

    def _remind_afternoon(self):
        afternoon = [c for c in self.today_courses if c.get("start_time", "") >= "12:00:00"]
        self.speaker.remind_afternoon(afternoon)

    def check_jwxt_cookie(self):
        logger.info("检查教务系统Cookie...")
        valid = self.jwxt.check_cookie_valid()
        if not valid:
            locked, lock_time = self.jwxt.is_locked()
            if locked:
                logger.warning(f"教务系统账号锁定/冷却中，解锁时间: {lock_time.strftime('%Y-%m-%d %H:%M:%S')}（自动重登已跳过，锁定期零请求）")
            elif self.jwxt._credentials_invalid:
                logger.warning("教务系统Cookie失效，凭证疑似失效请人工核对（自动重登已停止）")
            elif not (self.jwxt.account and self.jwxt.password):
                logger.warning("教务系统Cookie失效，无可用凭证，请在前端登录页配置账号密码")
            else:
                logger.warning("教务系统Cookie失效，自动重登未成功，将在下个检查周期重试；持续失效请在配置页面重新登录")
        return valid

    def check_xiaoai_cookie(self):
        logger.info("检查小爱音箱Cookie...")
        try:
            valid = self.speaker.check_cookie_valid()
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
        # COOKIE_REFRESH_INTERVAL 秒（默认 1800）→ 每 30 分钟检查双系统 Cookie；
        # 配置项此前未被消费，这里按 config 值生效（IntervalTrigger 避免 cron 对齐限制）。
        interval = config.COOKIE_REFRESH_INTERVAL
        self.scheduler.add_job(
            self.refresh_all,
            IntervalTrigger(seconds=interval),
            id="cookie_refresh",
            replace_existing=True,
        )
        self.scheduler.start()
        logger.info("Cookie管理器已启动，每 %d 秒（约 %d 分钟）检查一次", interval, interval // 60)
        try:
            self.check_xiaoai_cookie()
        except Exception as e:
            logger.error(f"启动时小爱Cookie检查失败: {e}")
        logger.info("调度器已启动")
        self.refresh_course_table()
        self.scheduler.add_job(self.refresh_course_table, CronTrigger(hour=6, minute=0), id="refresh_daily", replace_existing=True)
        logger.info("已设置每日06:00刷新课表")
        logger.info("已设置每日06:00刷新课表")

    def stop(self):
        """停止调度器（幂等）：未启动/已停止时 shutdown 抛 SchedulerNotRunningError，
        这里兜住，保证本地 finally 与 gunicorn atexit 重复调用都安全。

        wait=False：SIGTERM 停机不等待长耗时 job（课表刷新/播报），
        后台线程为 daemon，进程可立即退出。
        """
        try:
            self.scheduler.shutdown(wait=False)
        except SchedulerNotRunningError:
            logger.info("调度器未运行，跳过停止")
            return
        except Exception as e:
            logger.error(f"停止调度器异常（已忽略）: {e}")
            return
        logger.info("调度器已停止")
        logger.info("Cookie管理器已停止")
