"""JWXTClient 门面：公开成员签名冻结 + 跨域流程编排（语义等价自根 jwxt.py）。"""
import logging
import time
from datetime import datetime, timedelta

import requests

from lit import config
from lit.jwxt import auth, cookies, courses, lock, xnxqdm

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

    def set_manual_cookie(self, cookie_str, account=None):
        if account:
            self.account = account
        self.session.cookies.clear()
        for pair in cookie_str.split(";"):
            pair = pair.strip()
            if "=" in pair:
                k, v = pair.split("=", 1)
                self.session.cookies.set(k.strip(), v.strip())
        cookies._save_cookies(self)
        self.logged_in = True
        if not self.xnxqdm:
            xnxqdm._fetch_xnxqdm(self)
        logger.info("手动设置教务系统Cookie成功")
        return True

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

    def get_today_courses(self):
        course_list = self.get_course_table()
        if not course_list:
            return []
        today_weekday = datetime.now().weekday() + 1
        today_courses = [c for c in course_list if c.get("day") == today_weekday]
        today_courses.sort(key=lambda x: x.get("section_start", 0))
        return today_courses

    def get_status(self):
        locked, lock_time = self.is_locked()
        return {
            "logged_in": self.logged_in,
            "cookie_valid": self.cookie_valid,
            "login_time": self.login_time.isoformat() if self.login_time else None,
            "account": self.account,
            "locked": locked,
            "lock_time": lock_time.isoformat() if lock_time else None,
            "failed_attempts": self.failed_attempts,
            "xnxqdm": self.xnxqdm,
        }