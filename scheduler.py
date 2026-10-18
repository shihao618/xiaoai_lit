import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from lit import config
from jwxt import JWXTClient
from xiaoai import XiaoAISpeaker

logger = logging.getLogger(__name__)


class CourseScheduler:
    def __init__(self, jwxt_client=None, xiaoai_speaker=None):
        self.scheduler = BackgroundScheduler()
        self.jwxt = jwxt_client or JWXTClient()
        self.speaker = xiaoai_speaker or XiaoAISpeaker()
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

    def start(self):
        self.scheduler.start()
        logger.info("调度器已启动")
        self.refresh_course_table()
        self.scheduler.add_job(self.refresh_course_table, CronTrigger(hour=6, minute=0), id="refresh_daily", replace_existing=True)
        logger.info("已设置每日06:00刷新课表")

    def stop(self):
        self.scheduler.shutdown()
        logger.info("调度器已停止")