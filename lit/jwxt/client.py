"""JWXTClient 门面：公开成员签名冻结 + 跨域流程编排（jwxt_pure_api 重组）。

四链路编排（design 2.1.3）：
①课表刷新：fetch_today_context 定位今日 → 学期切换检测 →
  fetch_week_courses(zc=N) 当周直查 → 五元组缓存落盘；
②登录：凭证解析（持久化优先）→ OCR+AES → cookies 落盘 + 凭证持久化；
③自动重登：四重前置判定（锁定敬畏/凭证错误/无凭证/预算）+ 失败分级；
  失效后每周期自动重试，验证码错误换图重试，网络异常不误判失效；
④学期切换：TodayContext.xnxqdm ≠ 缓存值 → 更新域 + 缓存重查。
"""
import logging
from datetime import date, datetime, timedelta

from lit import config
from lit.jwxt import api, credentials as cred, lock, session as session_mod, state

logger = logging.getLogger(__name__)

MAX_FAILED_ATTEMPTS = lock.MAX_FAILED_ATTEMPTS
COOLDOWN_MINUTES = lock.COOLDOWN_MINUTES

RELOGIN_DAILY_BUDGET = 5
# 自动重登时登录尝试次数（每次换新验证码；OCR 偶发识别错误不至于等到下个周期）
RELOGIN_LOGIN_ATTEMPTS = 2

_CREDENTIAL_ERROR_MARKERS = ("账号", "密码", "用户名")

# 会话探活三态：网络异常不应被当作会话失效（否则会误耗重登预算）
_PROBE_OK = "ok"
_PROBE_INVALID = "invalid"
_PROBE_TRANSIENT = "transient"


class JWXTClient:
    def __init__(self, account=None, password=None):
        self.base_url = config.JWXT_BASE_URL
        resolved = cred.resolve_credentials(account or config.JWXT_ACCOUNT,
                                            password or config.JWXT_PASSWORD)
        if resolved is not None:
            self.account, self.password = resolved
        else:
            self.account = account or config.JWXT_ACCOUNT
            self.password = password or config.JWXT_PASSWORD
        self.xnxqdm = config.XNXQDM
        self.session = session_mod.create_session(self.base_url)
        self.logged_in = False
        self.login_time = None
        self.cookie_valid = False
        self.today_context = None
        self._week_courses = None
        self._week_courses_date = None
        self._failed_attempts = 0
        self._cooldown_until = None
        self._lock_until = None
        self._relogin_failures_today = 0
        self._relogin_failure_date = None
        self._relogin_tried_this_cycle = False
        self._credentials_invalid = False
        if not self.account:
            self.account = state.read_state().get("account", "")
        if session_mod.load_cookies(self.session, self.account):
            login_time = state.read_state().get("login_time")
            if login_time:
                try:
                    self.login_time = datetime.fromisoformat(login_time)
                except (TypeError, ValueError):
                    self.login_time = None
            self.cookie_valid = True
        lock._load_lock(self)
        if not self.xnxqdm:
            cached = state.read_state().get("xnxqdm", "")
            if cached:
                self.xnxqdm = cached
                logger.info(f"加载缓存学期代码: {self.xnxqdm}")

    @property
    def failed_attempts(self) -> int:
        return self._failed_attempts

    def recognize_captcha(self, image: bytes) -> str:
        return api.login.recognize_captcha(image)

    def is_locked(self):
        now = datetime.now()
        if self._lock_until and now < self._lock_until:
            return True, self._lock_until
        if self._cooldown_until and now < self._cooldown_until:
            return True, self._cooldown_until
        return False, None

    def check_cookie_valid(self):
        """周期校验入口：会话有效则探活确认；失效/已知失效则尝试自动重登。

        修复点（掉线后不再"永久失效"）：
        - 每次调用都重置周期标志，因此自动重登失败后下个检查周期仍会重试；
        - 已知 cookie_valid=False 时也走自动重登，而不是直接返回 False
          （旧实现该分支导致 Cookie 失效标志粘住、永不恢复）；
        - 网络异常与会话失效区分：网络异常不改会话状态、不消耗重登预算。
        """
        self._relogin_tried_this_cycle = False
        if self.cookie_valid:
            status = self._probe_session()
            if status == _PROBE_OK:
                logger.info("教务系统Cookie有效")
                self.logged_in = True
                return True
            if status == _PROBE_TRANSIENT:
                return False
            logger.warning("教务系统Cookie失效，尝试自动重登")
            self.cookie_valid = False
        return self._try_auto_relogin()

    def _probe_session(self):
        """curMonthXnxq 轻量探活：返回 _PROBE_OK / _PROBE_INVALID / _PROBE_TRANSIENT。

        code<0（含返回登录页导致的 JSON 解析失败）与结构异常 → 会话失效；
        网络层异常/超时 → 暂时性，不判失效（避免误耗自动重登预算）。
        """
        try:
            ctx = api.term_code.fetch_today_context(self.session, self.base_url, date.today())
        except ValueError as e:
            logger.warning(f"教务系统Cookie检查失败（会话失效）: {e}")
            return _PROBE_INVALID
        except Exception as e:
            logger.warning(f"教务系统Cookie检查失败（网络异常，暂不判失效）: {e}")
            return _PROBE_TRANSIENT
        if ctx is None:
            logger.warning("教务系统Cookie检查失败: 学期上下文结构异常")
            return _PROBE_INVALID
        self.today_context = ctx
        if ctx.xnxqdm and ctx.xnxqdm != self.xnxqdm:
            self.xnxqdm = ctx.xnxqdm
            state.update_state({"xnxqdm": ctx.xnxqdm, "updated": datetime.now().isoformat()})
        return _PROBE_OK

    def get_captcha_image(self):
        return api.login.get_captcha_image(self.session, self.base_url)

    def login(self, verifycode=None, max_retries=1):
        if not self.account or not self.password:
            return {"success": False, "message": "请先填写账号和密码"}

        locked, lock_time = self.is_locked()
        if locked:
            msg = f"账号处于锁定/冷却状态，解锁时间: {lock_time.strftime('%Y-%m-%d %H:%M:%S')}"
            logger.warning(msg)
            return {"success": False, "message": msg, "locked": True, "lock_time": lock_time.isoformat()}

        if self.cookie_valid and self._probe_session() == _PROBE_OK:
            return {"success": True, "message": "Cookie有效，无需重新登录"}

        last_msg = "登录失败"
        for attempt in range(max_retries):
            try:
                if verifycode:
                    code = verifycode
                    verifycode = None
                else:
                    code = api.login.recognize_captcha(self.get_captcha_image())
                    if not code or len(code) != 4:
                        continue

                encrypted_pwd = api.login.encrypt_password(self.password, code)
                result = api.login.do_login(self.session, self.base_url,
                                            self.account, encrypted_pwd, code)

                if result.get("code", -1) >= 0:
                    self.logged_in = True
                    self.cookie_valid = True
                    self.login_time = datetime.now()
                    lock._clear_lock(self)
                    session_mod.save_cookies(self.session, self.account)
                    try:
                        ctx = api.term_code.fetch_today_context(self.session, self.base_url, date.today())
                        if ctx is not None:
                            self.today_context = ctx
                            if ctx.xnxqdm:
                                self.xnxqdm = ctx.xnxqdm
                                state.update_state({"xnxqdm": ctx.xnxqdm,
                                                    "updated": datetime.now().isoformat()})
                                logger.info(f"自动获取学期代码: {self.xnxqdm}")
                    except Exception as e:
                        logger.warning(f"登录后获取学期代码失败: {e}")
                    cred.save_credentials(self.account, self.password)
                    self._credentials_invalid = False
                    logger.info("教务系统登录成功")
                    return {"success": True, "message": "登录成功"}

                msg = result.get("message", "登录失败")
                last_msg = msg

                # 验证码识别错误属临时性：不累计锁定计数、不触发冷却，换一张重试即可
                if "验证码" in msg:
                    logger.warning(f"登录验证码错误，重试: {msg}")
                    continue

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

                if any(marker in msg for marker in _CREDENTIAL_ERROR_MARKERS):
                    logger.warning(f"登录失败（凭证错误，不再重试）: {msg}")
                    return {"success": False, "message": msg}

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

    def _try_auto_relogin(self) -> bool:
        """链路③：四重前置判定（任一不过跳过）+ 预算内执行 + 失败分级（Q6）。"""
        today = date.today()
        if self._relogin_failure_date != today:
            self._relogin_failures_today = 0
            self._relogin_failure_date = today
        locked, lock_time = self.is_locked()
        if locked:
            logger.warning(f"锁定期跳过自动重登（零登录请求，解锁时间: {lock_time}）")
            return False
        if self._credentials_invalid:
            logger.warning("凭证疑似失效请人工核对（自动重登已停止）")
            return False
        resolved = cred.resolve_credentials(config.JWXT_ACCOUNT, config.JWXT_PASSWORD)
        if resolved is None:
            logger.warning("无可用凭证（持久化与环境变量均未配置），等待人工登录")
            return False
        if self._relogin_failures_today >= RELOGIN_DAILY_BUDGET or self._relogin_tried_this_cycle:
            logger.warning("自动重登预算耗尽（当日已达上限或本周期已尝试），静默等待恢复")
            return False
        self._relogin_tried_this_cycle = True
        self.account, self.password = resolved
        result = self.login(max_retries=RELOGIN_LOGIN_ATTEMPTS)
        if result.get("success"):
            logger.info("自动重登成功")
            return True
        if result.get("locked"):
            logger.warning("自动重登触发锁定/冷却，锁定期零请求")
            return False
        msg = str(result.get("message", ""))
        if any(marker in msg for marker in _CREDENTIAL_ERROR_MARKERS):
            self._credentials_invalid = True
            logger.warning("凭证疑似失效请人工核对（自动重登立即停止，直至人工登录成功）")
            return False
        self._relogin_failures_today += 1
        logger.warning(f"自动重登临时失败（当日第 {self._relogin_failures_today} 次）")
        return False

    def _fallback_to_cache(self, today):
        """F4/F10 缓存回退：日期窗口覆盖 + 已知今日上下文时精确比对。"""
        kwargs = {}
        ctx = self.today_context
        if ctx is not None and ctx.rq == today and ctx.in_term and ctx.zc > 0:
            if ctx.xnxqdm:
                kwargs["xnxqdm"] = ctx.xnxqdm
            kwargs["zc"] = ctx.zc
        cached = api.week_table.load_cache_for_today(today, **kwargs)
        if cached is not None:
            logger.warning("周课表查询失败，使用缓存")
            return cached
        logger.error("课表数据不可用（缓存缺失/过期）")
        return None

    def _refresh_week_courses(self, today):
        """链路①核心：今日定位 → 学期切换检测（链路④）→ 当周查询 → 五元组落盘。"""
        try:
            ctx = api.term_code.fetch_today_context(self.session, self.base_url, today)
        except ValueError:
            raise
        except Exception as e:
            logger.warning(f"学期代码查询网络异常: {e}")
            ctx = None
            if self.today_context is not None and self.today_context.rq == today \
                    and self.today_context.in_term and self.today_context.zc > 0:
                xnxqdm = self.today_context.xnxqdm or self.xnxqdm
                zc = self.today_context.zc
            else:
                xnxqdm, zc = None, None
            if not xnxqdm or not zc:
                return self._fallback_to_cache(today)
            logger.warning(f"降级使用缓存周次 zc={zc}（周次无法核对）")
        if ctx is not None:
            self.today_context = ctx
            if not ctx.in_term:
                logger.info("学期外日期，今日无课")
                return []
            xnxqdm = ctx.xnxqdm or self.xnxqdm
            zc = ctx.zc
            if ctx.xnxqdm and ctx.xnxqdm != self.xnxqdm:
                logger.info(f"学期切换: {self.xnxqdm} → {ctx.xnxqdm}，缓存重查")
                self.xnxqdm = ctx.xnxqdm
                state.update_state({"xnxqdm": ctx.xnxqdm, "updated": datetime.now().isoformat()})
        courses = api.week_table.fetch_week_courses(self.session, self.base_url, xnxqdm, zc)
        if not courses:
            logger.info("本教学周无课程")
        week_start = today - timedelta(days=today.weekday())
        week_end = week_start + timedelta(days=6)
        api.week_table.save_cache(xnxqdm, zc, week_start, week_end, courses)
        logger.info(f"获取课表成功，共 {len(courses)} 条课程记录")
        return courses

    def get_course_table(self):
        """链路①编排：返回当周课程列表（契约 #3 当周语义，空列表合法 F5）。"""
        today = date.today()
        if not self.account or not self.password:
            logger.warning("账号密码未配置，跳过课表获取")
            return self._fallback_to_cache(today)
        # 每次课表刷新视为新周期：确保"上周期已尝试"标志不阻塞本轮恢复
        self._relogin_tried_this_cycle = False
        if not self.logged_in and not self.check_cookie_valid():
            return self._fallback_to_cache(today)
        for attempt in range(2):
            try:
                courses = self._refresh_week_courses(today)
                if courses is not None:
                    self._week_courses = courses
                    self._week_courses_date = today.isoformat()
                return courses
            except ValueError as e:
                logger.warning(f"课表查询会话失效: {e}")
                if attempt == 0 and self._try_auto_relogin():
                    continue
                return self._fallback_to_cache(today)
            except Exception as e:
                logger.error(f"获取课表异常: {e}")
                return self._fallback_to_cache(today)
        return self._fallback_to_cache(today)

    def get_today_courses(self):
        """qsrq 直过滤单路径（H7）：当周缓存内存态或 get_course_table 结果
        ∩ date==today，零星期/周次计算。"""
        today_iso = date.today().isoformat()
        if self._week_courses is not None and self._week_courses_date == today_iso:
            course_list = self._week_courses
        else:
            course_list = self.get_course_table()
        if not course_list:
            return []
        today_courses = [c for c in course_list if c.get("date") == today_iso]
        today_courses.sort(key=lambda x: x.get("start_time", ""))
        return today_courses

    def get_status(self):
        """9 键契约（Q8 ②）：current_week 源自 curMonthXnxq 内存态。"""
        locked, lock_time = self.is_locked()
        week = None
        if self.today_context is not None and self.today_context.in_term:
            week = self.today_context.zc
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
        }