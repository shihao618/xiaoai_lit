"""JWXTClient 门面：公开成员签名冻结 + 跨域流程编排（语义等价自根 jwxt.py）。"""
import logging
import time
from datetime import date, datetime, timedelta

import requests

from lit import config
from lit.jwxt import auth, cookies, courses, lock, term, xnxqdm

logger = logging.getLogger(__name__)

MAX_FAILED_ATTEMPTS = lock.MAX_FAILED_ATTEMPTS
COOLDOWN_MINUTES = lock.COOLDOWN_MINUTES


class JWXTClient:
    def __init__(self, account=None, password=None):
        self.base_url = config.JWXT_BASE_URL
        self.account = account or config.JWXT_ACCOUNT
        self.password = password or config.JWXT_PASSWORD
        self.xnxqdm = config.XNXQDM
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": self.base_url + "/",
        })
        self.logged_in = False
        self.login_time = None
        self.cookie_valid = False
        self._failed_attempts = 0
        self._cooldown_until = None
        self._lock_until = None
        cookies._load_cookies(self)
        lock._load_lock(self)
        if not self.xnxqdm:
            xnxqdm._load_xnxqdm(self)

    @property
    def failed_attempts(self) -> int:
        return self._failed_attempts

    def recognize_captcha(self, image: bytes) -> str:
        return auth.recognize_captcha(image)

    def is_locked(self):
        now = datetime.now()
        if self._lock_until and now < self._lock_until:
            return True, self._lock_until
        if self._cooldown_until and now < self._cooldown_until:
            return True, self._cooldown_until
        return False, None


    def check_cookie_valid(self):
        if not self.cookie_valid:
            return False
        try:
            if not self.xnxqdm:
                if not xnxqdm._fetch_xnxqdm(self):
                    logger.warning("无法获取学期代码，Cookie可能失效")
                    self.cookie_valid = False
                    return False
            resp = self.session.post(
                f"{self.base_url}/new/student/xsgrkb/getCalendarWeekDatas",
                data={"xnxqdm": self.xnxqdm, "zc": "", "szxqdm": "", "yxdm": ""},
                headers={"X-Requested-With": "XMLHttpRequest",
                         "Referer": f"{self.base_url}/new/student/xsgrkb/week.page?xnxqdm={self.xnxqdm}"},
                timeout=10,
            )
            result = resp.json()
            if result.get("code", -1) >= 0:
                logger.info("教务系统Cookie有效")
                self.logged_in = True
                return True
            else:
                logger.warning(f"教务系统Cookie失效: {result.get('message')}")
                self.cookie_valid = False
                return False
        except Exception as e:
            logger.warning(f"教务系统Cookie检查失败: {e}")
            self.cookie_valid = False
            return False

    def get_captcha_image(self):
        url = f"{self.base_url}/yzm?d={int(time.time() * 1000)}"
        resp = self.session.get(url)
        return resp.content

    def login(self, verifycode=None, max_retries=1):
        if not self.account or not self.password:
            return {"success": False, "message": "请先填写账号和密码"}

        locked, lock_time = self.is_locked()
        if locked:
            msg = f"账号处于锁定/冷却状态，解锁时间: {lock_time.strftime('%Y-%m-%d %H:%M:%S')}"
            logger.warning(msg)
            return {"success": False, "message": msg, "locked": True, "lock_time": lock_time.isoformat()}

        if self.check_cookie_valid():
            return {"success": True, "message": "Cookie有效，无需重新登录"}

        last_msg = "登录失败"
        for attempt in range(max_retries):
            try:
                if verifycode:
                    code = verifycode
                    verifycode = None
                else:
                    code = auth._get_captcha_ocr(self)
                    if not code or len(code) != 4:
                        continue

                encrypted_pwd = auth._encrypt_password(self.password, code)
                resp = self.session.post(
                    f"{self.base_url}/new/login",
                    data={"account": self.account, "pwd": encrypted_pwd, "verifycode": code},
                    timeout=15,
                )
                result = resp.json()

                if result.get("code", -1) >= 0:
                    self.logged_in = True
                    lock._clear_lock(self)
                    cookies._save_cookies(self)
                    xnxqdm._fetch_xnxqdm(self)
                    logger.info("教务系统登录成功")
                    return {"success": True, "message": "登录成功"}

                msg = result.get("message", "登录失败")
                last_msg = msg
                self._failed_attempts += 1
                logger.warning(f"登录失败({self._failed_attempts}): {msg}")

                if "锁定" in msg or "锁" in msg:
                    lock_time = lock._parse_lock_time(msg)
                    if lock_time:
                        self._lock_until = lock_time
                    else:
                        self._cooldown_until = datetime.now() + timedelta(minutes=COOLDOWN_MINUTES)
                    lock._save_lock(self)
                    logger.error(f"账号被锁定，停止登录尝试，解锁时间: {self._lock_until or self._cooldown_until}")
                    return {"success": False, "message": msg, "locked": True,
                            "lock_time": (self._lock_until or self._cooldown_until).isoformat()}

                if self._failed_attempts >= MAX_FAILED_ATTEMPTS:
                    self._cooldown_until = datetime.now() + timedelta(minutes=COOLDOWN_MINUTES)
                    lock._save_lock(self)
                    logger.error(f"连续失败{self._failed_attempts}次，进入{COOLDOWN_MINUTES}分钟冷却期")
                    return {"success": False, "message": f"连续登录失败，已暂停登录{COOLDOWN_MINUTES}分钟",
                            "locked": True, "lock_time": self._cooldown_until.isoformat()}

            except Exception as e:
                last_msg = str(e)
                logger.error(f"登录异常: {e}")

        return {"success": False, "message": last_msg}

    def get_course_table(self):
        if not self.account or not self.password:
            logger.warning("账号密码未配置，跳过课表获取")
            return courses._load_cache()
        if not self.logged_in and not self.check_cookie_valid():
            result = self.login()
            if not result.get("success"):
                return None

        try:
            self.session.headers.update({
                "Referer": f"{self.base_url}/new/student/xsgrkb/week.page?xnxqdm={self.xnxqdm}",
                "X-Requested-With": "XMLHttpRequest",
            })
            resp = self.session.post(
                f"{self.base_url}/new/student/xsgrkb/getCalendarWeekDatas",
                data={"xnxqdm": self.xnxqdm, "zc": "", "szxqdm": "", "yxdm": ""},
                timeout=15,
            )
            result = resp.json()

            if result.get("code", -1) < 0:
                logger.warning(f"课表查询失败: {result.get('message')}")
                self.cookie_valid = False
                login_result = self.login()
                if login_result.get("success"):
                    resp = self.session.post(
                        f"{self.base_url}/new/student/xsgrkb/getCalendarWeekDatas",
                        data={"xnxqdm": self.xnxqdm, "zc": "", "szxqdm": "", "yxdm": ""},
                        timeout=15,
                    )
                    result = resp.json()
                else:
                    return courses._load_cache()

            raw_courses = result.get("data", [])
            parsed = courses.parse_courses(raw_courses)
            if parsed:
                courses._save_cache(parsed)
                logger.info(f"获取课表成功，共 {len(parsed)} 条课程记录")
                return parsed
            return courses._load_cache()
        except Exception as e:
            logger.error(f"获取课表异常: {e}")
            return courses._load_cache()

    def ensure_term_anchor(self, course_list: list[dict]) -> bool:
        """锚点保障入口（tasks 刷新编排唯一调用点）：
        有效锚点存在 → True（零开销跳过）；缺失 → _probe_term_anchor；
        整体 try-except 全捕获——探测任何失败不阻断刷新主流程。"""
        try:
            if term.get_effective_anchor(self.xnxqdm) is not None:
                return True
            return self._probe_term_anchor(course_list)
        except Exception as e:
            logger.warning(f"学期锚点保障异常（不影响刷新主流程）: {e}")
            return False

    def _probe_term_anchor(self, course_list: list[dict]) -> bool:
        """取样周次（前 3 个有课小周次）逐周探测：查询 → 反推校验 →
        成功即 save_anchor(source="auto") 并记探测依据日志；全部失败返回 False。"""
        weeks = term.available_probe_weeks(course_list)
        if not weeks:
            logger.warning("课表无周次数据，无法探测学期锚点")
            return False
        for n in weeks[:term.PROBE_SAMPLE_SIZE]:
            try:
                raw = self._fetch_week_raw(n)
            except Exception as e:
                logger.warning(f"锚点探测请求失败(第 {n} 周): {e}")
                continue
            votes = term._vote_anchor(raw, n)
            anchor = term.anchor_from_week_response(raw, n)
            if anchor is None:
                logger.warning(f"第 {n} 周数据未通过多数一致校验")
                continue
            term.save_anchor(anchor, self.xnxqdm, "auto")
            total = sum(votes.values())
            logger.info(
                f"锚点探测成功: 第1教学周周一={anchor.isoformat()}"
                f"（取样第 {n} 周, 票数 {votes[anchor]}/{total}）")
            return True
        logger.warning("锚点探测放弃（本次刷新），下次刷新重试")
        return False

    def _fetch_week_raw(self, week_n: int) -> list[dict]:
        """指定周次 POST getCalendarWeekDatas（zc=str(week_n)，其余参数与
        get_course_table 完全一致，timeout=15）；code<0 抛 ValueError，
        网络异常自然上抛——均由 _probe_term_anchor 捕获换周。
        返回 data 数组原貌（含 qsrq 日期字段，不经过 parse_courses）。"""
        self.session.headers.update({
            "Referer": f"{self.base_url}/new/student/xsgrkb/week.page?xnxqdm={self.xnxqdm}",
            "X-Requested-With": "XMLHttpRequest",
        })
        resp = self.session.post(
            f"{self.base_url}/new/student/xsgrkb/getCalendarWeekDatas",
            data={"xnxqdm": self.xnxqdm, "zc": str(week_n), "szxqdm": "", "yxdm": ""},
            timeout=15,
        )
        result = resp.json()
        if result.get("code", -1) < 0:
            raise ValueError(f"课表周次查询失败: {result.get('message')}")
        return result.get("data", [])

    def get_today_courses(self):
        course_list = self.get_course_table()
        if not course_list:
            return []
        today = date.today()
        today_weekday = today.weekday() + 1
        anchor = term.get_effective_anchor(self.xnxqdm)
        if anchor is not None:
            week = term.current_week(anchor, today)
            if week <= 0:
                logger.info(f"学期外日期（第 {week} 周），今日无课")
                return []
            logger.info(f"当前第 {week} 周（锚点 {anchor.isoformat()}，学期 {self.xnxqdm}）")
            today_courses = term.filter_today(course_list, today_weekday, week)
        else:
            logger.warning("学期锚点未生效，周次过滤未启用（仅按星期过滤；等待探测或手动配置）")
            today_courses = [c for c in course_list if c.get("day") == today_weekday]
        today_courses.sort(key=lambda x: x.get("section_start", 0))
        return today_courses

    def get_status(self):
        locked, lock_time = self.is_locked()
        anchor = term.get_effective_anchor(self.xnxqdm)
        week = term.current_week(anchor, date.today()) if anchor is not None else None
        return {
            "logged_in": self.logged_in,
            "cookie_valid": self.cookie_valid,
            "login_time": self.login_time.isoformat() if self.login_time else None,
            "account": self.account,
            "locked": locked,
            "lock_time": lock_time.isoformat() if lock_time else None,
            "failed_attempts": self.failed_attempts,
            "xnxqdm": self.xnxqdm,
            "current_week": week,
            "term_start_monday": anchor.isoformat() if anchor is not None else None,
            "week_filter_enabled": anchor is not None,
        }