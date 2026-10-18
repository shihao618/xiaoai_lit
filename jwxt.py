import os
import re
import json
import time
import logging
import requests
import ddddocr
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from datetime import datetime, timedelta
import config

logger = logging.getLogger(__name__)

MAX_FAILED_ATTEMPTS = 3
COOLDOWN_MINUTES = 30


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
        self.ocr = None
        self.logged_in = False
        self.login_time = None
        self.cookie_valid = False
        self._failed_attempts = 0
        self._cooldown_until = None
        self._lock_until = None
        self._load_cookies()
        self._load_lock()
        if not self.xnxqdm:
            self._load_xnxqdm()

    def _lock_file(self):
        return os.path.join(config.DATA_DIR, "jwxt_lock.json")

    def _save_lock(self):
        try:
            os.makedirs(config.DATA_DIR, exist_ok=True)
            data = {
                "account": self.account,
                "lock_until": (self._lock_until or self._cooldown_until).isoformat() if (self._lock_until or self._cooldown_until) else None,
                "failed_attempts": self._failed_attempts,
            }
            with open(self._lock_file(), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"保存锁定状态失败: {e}")

    def _load_lock(self):
        lock_file = self._lock_file()
        if os.path.exists(lock_file):
            try:
                with open(lock_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                saved_account = data.get("account", "")
                if not self.account:
                    self.account = saved_account
                if saved_account == self.account and data.get("lock_until"):
                    self._lock_until = datetime.fromisoformat(data["lock_until"])
                    self._failed_attempts = data.get("failed_attempts", 0)
                    if datetime.now() < self._lock_until:
                        logger.warning(f"检测到持久化的锁定状态，解锁时间: {self._lock_until}")
                    else:
                        self._lock_until = None
            except Exception as e:
                logger.warning(f"加载锁定状态失败: {e}")

    def _clear_lock(self):
        self._failed_attempts = 0
        self._cooldown_until = None
        self._lock_until = None
        lock_file = self._lock_file()
        if os.path.exists(lock_file):
            try:
                os.remove(lock_file)
            except Exception:
                pass

    def is_locked(self):
        now = datetime.now()
        if self._lock_until and now < self._lock_until:
            return True, self._lock_until
        if self._cooldown_until and now < self._cooldown_until:
            return True, self._cooldown_until
        return False, None

    def _parse_lock_time(self, message):
        m = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", message)
        if m:
            try:
                return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
            except Exception:
                return None
        return None

    def _init_ocr(self):
        if self.ocr is None:
            self.ocr = ddddocr.DdddOcr(show_ad=False)

    def _xnxqdm_file(self):
        return os.path.join(config.DATA_DIR, "jwxt_xnxqdm.json")

    def _save_xnxqdm(self):
        try:
            os.makedirs(config.DATA_DIR, exist_ok=True)
            with open(self._xnxqdm_file(), "w", encoding="utf-8") as f:
                json.dump({"xnxqdm": self.xnxqdm, "updated": datetime.now().isoformat()}, f)
        except Exception as e:
            logger.warning(f"保存学期代码失败: {e}")

    def _load_xnxqdm(self):
        f = self._xnxqdm_file()
        if os.path.exists(f):
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                self.xnxqdm = data.get("xnxqdm", "")
                if self.xnxqdm:
                    logger.info(f"加载缓存学期代码: {self.xnxqdm}")
            except Exception as e:
                logger.warning(f"加载学期代码失败: {e}")

    def _fetch_xnxqdm(self):
        try:
            resp = self.session.get(
                f"{self.base_url}/new/student/xsgrkb/week.page",
                timeout=15,
            )
            html = resp.text
            m = re.search(r"""<option[^>]*value=['"](\d{6,})['"][^>]*selected""", html)
            if m:
                self.xnxqdm = m.group(1)
                self._save_xnxqdm()
                logger.info(f"自动获取学期代码: {self.xnxqdm}")
                return True
            m = re.search(r"""xnxqdm['"]\s*[:=]\s*['"](\d{6,})['"]""", html)
            if m:
                self.xnxqdm = m.group(1)
                self._save_xnxqdm()
                logger.info(f"自动获取学期代码(从变量): {self.xnxqdm}")
                return True
            logger.warning("无法从课表页面解析学期代码")
            return False
        except Exception as e:
            logger.warning(f"获取学期代码失败: {e}")
            return False

    def _cookie_file(self):
        return os.path.join(config.DATA_DIR, "jwxt_cookies.json")

    def _save_cookies(self):
        os.makedirs(config.DATA_DIR, exist_ok=True)
        cookies = self.session.cookies.get_dict()
        data = {
            "cookies": cookies,
            "login_time": datetime.now().isoformat(),
            "account": self.account,
        }
        with open(self._cookie_file(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        self.cookie_valid = True
        self.login_time = datetime.now()

    def set_manual_cookie(self, cookie_str, account=None):
        if account:
            self.account = account
        self.session.cookies.clear()
        for pair in cookie_str.split(";"):
            pair = pair.strip()
            if "=" in pair:
                k, v = pair.split("=", 1)
                self.session.cookies.set(k.strip(), v.strip())
        self._save_cookies()
        self.logged_in = True
        if not self.xnxqdm:
            self._fetch_xnxqdm()
        logger.info("手动设置教务系统Cookie成功")
        return True

    def _load_cookies(self):
        cookie_file = self._cookie_file()
        if os.path.exists(cookie_file):
            try:
                with open(cookie_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                saved_account = data.get("account", "")
                if not self.account:
                    self.account = saved_account
                if saved_account == self.account:
                    for k, v in data.get("cookies", {}).items():
                        self.session.cookies.set(k, v)
                    self.login_time = datetime.fromisoformat(data.get("login_time", datetime.now().isoformat()))
                    logger.info(f"加载教务系统缓存Cookie，登录于 {self.login_time}")
                    self.cookie_valid = True
                else:
                    logger.info(f"Cookie文件账号({saved_account})与当前账号({self.account})不匹配，跳过加载")
            except Exception as e:
                logger.warning(f"加载Cookie失败: {e}")

    def check_cookie_valid(self):
        if not self.cookie_valid:
            return False
        try:
            if not self.xnxqdm:
                if not self._fetch_xnxqdm():
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

    def _get_captcha_ocr(self):
        self._init_ocr()
        image = self.get_captcha_image()
        result = self.ocr.classification(image)
        return result

    def _encrypt_password(self, password, verifycode):
        key = (verifycode * 4).encode("utf-8")
        cipher = AES.new(key, AES.MODE_ECB)
        encrypted = cipher.encrypt(pad(password.encode("utf-8"), AES.block_size))
        return encrypted.hex()

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
                    code = self._get_captcha_ocr()
                    if not code or len(code) != 4:
                        continue

                encrypted_pwd = self._encrypt_password(self.password, code)
                resp = self.session.post(
                    f"{self.base_url}/new/login",
                    data={"account": self.account, "pwd": encrypted_pwd, "verifycode": code},
                    timeout=15,
                )
                result = resp.json()

                if result.get("code", -1) >= 0:
                    self.logged_in = True
                    self._clear_lock()
                    self._save_cookies()
                    self._fetch_xnxqdm()
                    logger.info("教务系统登录成功")
                    return {"success": True, "message": "登录成功"}

                msg = result.get("message", "登录失败")
                last_msg = msg
                self._failed_attempts += 1
                logger.warning(f"登录失败({self._failed_attempts}): {msg}")

                if "锁定" in msg or "锁" in msg:
                    lock_time = self._parse_lock_time(msg)
                    if lock_time:
                        self._lock_until = lock_time
                    else:
                        self._cooldown_until = datetime.now() + timedelta(minutes=COOLDOWN_MINUTES)
                    self._save_lock()
                    logger.error(f"账号被锁定，停止登录尝试，解锁时间: {self._lock_until or self._cooldown_until}")
                    return {"success": False, "message": msg, "locked": True,
                            "lock_time": (self._lock_until or self._cooldown_until).isoformat()}

                if self._failed_attempts >= MAX_FAILED_ATTEMPTS:
                    self._cooldown_until = datetime.now() + timedelta(minutes=COOLDOWN_MINUTES)
                    self._save_lock()
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
            return self._load_cache()
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
                    return self._load_cache()

            raw_courses = result.get("data", [])
            courses = self._parse_courses(raw_courses)
            if courses:
                self._save_cache(courses)
                logger.info(f"获取课表成功，共 {len(courses)} 条课程记录")
                return courses
            return self._load_cache()
        except Exception as e:
            logger.error(f"获取课表异常: {e}")
            return self._load_cache()

    def _parse_courses(self, raw_courses):
        courses = []
        for item in raw_courses:
            ps = int(item.get("ps", "0"))
            pe = int(item.get("pe", "0"))
            xq = int(item.get("xq", "0"))
            zc_str = item.get("zc", "")
            zc_list = [int(z) for z in zc_str.split(",") if z.strip().isdigit()]
            courses.append({
                "name": item.get("kcmc", ""),
                "section_start": ps,
                "section_end": pe,
                "day": xq,
                "start_time": item.get("qssj", ""),
                "end_time": item.get("jssj", ""),
                "classroom": item.get("jxcdmc", ""),
                "teacher": item.get("teaxms", ""),
                "weeks": zc_list,
                "week_str": zc_str,
            })
        return courses

    def _save_cache(self, courses):
        os.makedirs(config.CACHE_DIR, exist_ok=True)
        cache_file = os.path.join(config.CACHE_DIR, "course_table.json")
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump({"updated": datetime.now().isoformat(), "courses": courses}, f, ensure_ascii=False, indent=2)

    def _load_cache(self):
        cache_file = os.path.join(config.CACHE_DIR, "course_table.json")
        if os.path.exists(cache_file):
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("courses")
        return None

    def get_today_courses(self):
        courses = self.get_course_table()
        if not courses:
            return []
        today_weekday = datetime.now().weekday() + 1
        today_courses = [c for c in courses if c.get("day") == today_weekday]
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
            "failed_attempts": self._failed_attempts,
            "xnxqdm": self.xnxqdm,
        }