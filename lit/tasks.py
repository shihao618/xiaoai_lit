"""统一后台任务模块：课表刷新、课程提醒、双系统 Cookie 检查（单调度器，D5/D4）。

合并原 scheduler.CourseScheduler 与 cookie_manager.CookieManager，
单一 BackgroundScheduler 承载全部 job，触发时刻与启动时序零变化。
"""
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

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
        if courses:
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

        s12 = [c for c in self.today_courses if c.get("section_start", 99) in (1, 2)]
        s34 = [c for c in self.today_courses if c.get("section_start", 99) in (3, 4)]
        s56 = [c for c in self.today_courses if c.get("section_start", 99) in (5, 6)]
        s78 = [c for c in self.today_courses if c.get("section_start", 99) in (7, 8)]

        if s12:
            h, m = self._calc_remind_time(1)
            self.scheduler.add_job(self._remind_morning, CronTrigger(hour=h, minute=m), id="remind_morning", replace_existing=True)
            logger.info(f"已设置上午提醒: {h:02d}:{m:02d} (1-2节有课)")
        elif s34:
            h, m = self._calc_remind_time(3)
            self.scheduler.add_job(self._remind_morning, CronTrigger(hour=h, minute=m), id="remind_morning", replace_existing=True)
            logger.info(f"已设置上午提醒: {h:02d}:{m:02d} (1-2节无课，3-4节有课)")

        if s56:
            h, m = self._calc_remind_time(5)
            self.scheduler.add_job(self._remind_afternoon, CronTrigger(hour=h, minute=m), id="remind_afternoon", replace_existing=True)
            logger.info(f"已设置下午提醒: {h:02d}:{m:02d} (5-6节有课)")
        elif s78:
            h, m = self._calc_remind_time(7)
            self.scheduler.add_job(self._remind_afternoon, CronTrigger(hour=h, minute=m), id="remind_afternoon", replace_existing=True)
            logger.info(f"已设置下午提醒: {h:02d}:{m:02d} (5-6节无课，7-8节有课)")

    def _calc_remind_time(self, section):
        time_str = config.COURSE_TIME_MAP.get(section, "08:00")
        h, m = map(int, time_str.split(":"))
        total = h * 60 + m - config.REMIND_BEFORE_MINUTES
        return total // 60, total % 60

    def _remind_morning(self):
        morning = [c for c in self.today_courses if c.get("section_start", 99) <= 4]
        self.speaker.remind_morning(morning)

    def _remind_afternoon(self):
        afternoon = [c for c in self.today_courses if 5 <= c.get("section_start", 99) <= 8]
        self.speaker.remind_afternoon(afternoon)

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
        logger.info("调度器已启动")
        self.refresh_course_table()
        self.scheduler.add_job(self.refresh_course_table, CronTrigger(hour=6, minute=0), id="refresh_daily", replace_existing=True)
        logger.info("已设置每日06:00刷新课表")

    def stop(self):
        self.scheduler.shutdown()
        logger.info("调度器已停止")
        logger.info("Cookie管理器已停止")