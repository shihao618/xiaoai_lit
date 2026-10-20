"""XiaoAISpeaker 门面：凭证四态状态机、对外接口、云端原生播报编排。"""

import logging
import threading
import time
from datetime import datetime
from typing import List, Optional


from . import constants
from .account import XiaomiAccountClient
from .credentials import CredentialStore, Credentials
from .exceptions import (
    CommandRejectedError,
    CredentialRejectedError,
    RateLimitedError,
    RequestTimeoutError,
    XiaoAIError,
)
from .httpclient import XiaomiHTTPClient
from .mina import (MiNAClient, SpeakerDevice, USE_PLAY_MUSIC_HARDWARE,
                   is_speaker_hardware)
from .miot import MiOTClient
from .qrlogin import QRLoginManager, render_qr_png_data_uri

logger = logging.getLogger(__name__)

PROMPT_TEXT = "叮叮叮叮！上课提醒！"

_GRADE_LABELS = {1: "已确认播放", 2: "指令已接受未确认播放", 3: "播报失败"}


class XiaoAISpeaker:
    """对 app.py / tasks.py 提供稳定契约的门面。"""

    def __init__(self, *, http=None, account=None, mina=None, store=None, qr=None,
                 miot=None):
        self._lock = threading.RLock()
        self._store = store or CredentialStore()
        self._account = account or XiaomiAccountClient(http or XiaomiHTTPClient())
        self._mina = mina or MiNAClient(http or XiaomiHTTPClient())
        self._miot = miot or MiOTClient()
        self._qr = qr or QRLoginManager(self._account, self._on_qr_confirmed)
        self._cred: Optional[Credentials] = None
        self._state = constants.STATE_NO_CREDENTIAL
        self._devices: List[SpeakerDevice] = []
        self._username = ""
        self._login_time = None
        self._device_id_cache = None
        self._load_credentials_on_init()

    # ---------- 对外状态属性（app.py / blueprints 读取） ----------

    @property
    def logged_in(self) -> bool:
        with self._lock:
            return self._state == constants.STATE_LOGGED_IN

    @property
    def cookie_valid(self) -> bool:
        with self._lock:
            return self._state in (constants.STATE_LOGGED_IN,
                                   constants.STATE_PENDING_VERIFY)

    @property
    def login_time(self):
        with self._lock:
            return self._login_time

    @property
    def username(self) -> str:
        with self._lock:
            return self._username

    @property
    def device_id(self) -> str:
        with self._lock:
            if self._device_id_cache:
                return self._device_id_cache
            if self._cred and self._cred.default_device_id:
                return self._cred.default_device_id
            return ""

    @property
    def devices(self) -> list:
        with self._lock:
            return [d.to_api_dict() for d in self._devices]

    # ---------- 初始化与启动恢复 ----------

    def _load_credentials_on_init(self) -> None:
        try:
            cred = self._store.load()
        except Exception as e:
            logger.warning("加载小爱凭证失败: %s", e)
            return
        if cred is None:
            logger.info("无小爱凭证，请扫码登录")
            return
        if cred.rejected or not cred.user_id:
            logger.info("小爱凭证已标记失效或字段不全，视为未登录")
            return
        self._cred = cred
        self._username = str(cred.user_id)
        self._login_time = cred.refreshed_at
        if cred.default_device_id:
            self._device_id_cache = cred.default_device_id
        self._state = constants.STATE_PENDING_VERIFY
        logger.info("小爱凭证已加载（待验证）")

    def restore_on_start(self) -> None:
        threading.Thread(target=self._restore_worker, daemon=True,
                         name="xiaoai-restore").start()

    def _restore_worker(self) -> None:
        with self._lock:
            cred = self._cred
        if cred is None:
            logger.info("启动恢复：无小爱凭证")
            return
        if not cred.is_usable():
            logger.info("启动恢复：凭证不可用，保持待验证状态")
            return
        try:
            devices = self._mina.device_list(cred)
        except CredentialRejectedError as e:
            with self._lock:
                self._enter_rejected_locked()
            logger.warning("启动恢复：凭证已失效 (%s)", e)
            return
        except Exception as e:
            logger.warning("启动恢复：临时错误，凭证保留待验证: %s", e)
            return
        with self._lock:
            self._devices = devices
            self._state = constants.STATE_LOGGED_IN
            if not self._login_time:
                self._login_time = cred.refreshed_at or datetime.now()
        logger.info("启动恢复：小爱凭证有效，设备 %d 台", len(devices))

    # ---------- 状态机 ----------

    def _enter_rejected_locked(self) -> None:
        if self._state == constants.STATE_REJECTED:
            return
        self._state = constants.STATE_REJECTED
        self._cred = None
        self._devices = []
        self._login_time = None
        try:
            self._store.mark_rejected()
        except Exception as e:
            logger.warning("标记凭证失效失败: %s", e)
        logger.warning("小爱凭证被明确拒绝（70016/70001），需重新扫码登录")

    def _apply_error(self, exc: Exception) -> None:
        """错误分类落状态机：仅 CredentialRejectedError 迁移 REJECTED。"""
        if isinstance(exc, CredentialRejectedError):
            with self._lock:
                self._enter_rejected_locked()
            return
        logger.debug("小爱操作失败（凭证状态不变）: %s", exc)

    # ---------- 对 web 蓝图暴露的方法 ----------

    def check_cookie_valid(self) -> bool:
        with self._lock:
            if self._cred is None or self._state in (
                    constants.STATE_NO_CREDENTIAL, constants.STATE_REJECTED):
                return False
            cred = self._cred
        try:
            devices = self._mina.device_list(cred)
        except Exception as e:
            self._apply_error(e)
            logger.warning("小爱凭证校验失败: %s", e)
            return False
        with self._lock:
            self._devices = devices
            self._state = constants.STATE_LOGGED_IN
            if not self._login_time:
                self._login_time = cred.refreshed_at or datetime.now()
        return True

    def get_devices(self) -> list:
        with self._lock:
            cred = self._cred
            if cred is None or not cred.is_usable():
                raise XiaoAIError("小爱音箱未登录")
        try:
            devices = self._mina.device_list(cred)
        except Exception as e:
            self._apply_error(e)
            raise
        with self._lock:
            self._devices = devices
            if self._state == constants.STATE_PENDING_VERIFY:
                self._state = constants.STATE_LOGGED_IN
                if not self._login_time:
                    self._login_time = cred.refreshed_at or datetime.now()
        return [d.to_api_dict() for d in devices]

    def select_device(self, device_id) -> dict:
        with self._lock:
            self._device_id_cache = device_id
            if self._cred is not None:
                self._cred.default_device_id = device_id
                try:
                    self._store.save(self._cred)
                except Exception as e:
                    logger.warning("默认设备持久化失败: %s", e)
        logger.info("已选择小爱设备: %s", device_id)
        hardware = self._hardware_of(device_id)
        name = self._device_name_of(device_id) or ""
        # 设备不在已知列表（hardware 为 None）时按空串保守判定，原值披露
        is_speaker = is_speaker_hardware(hardware or "", name)
        warning = None
        if not is_speaker:
            warning = ("该设备疑似非物理音箱（hardware=%s），播报可能无声"
                       % (hardware if hardware is not None else "未知"))
        return {
            "device_id": device_id,
            "name": name,
            "hardware": hardware,
            "is_speaker": is_speaker,
            "warning": warning,
        }

    def get_status(self) -> dict:
        with self._lock:
            device_id = self._device_id_cache
            if not device_id and self._cred and self._cred.default_device_id:
                device_id = self._cred.default_device_id
            return {
                "logged_in": self._state == constants.STATE_LOGGED_IN,
                "cookie_valid": self._state in (
                    constants.STATE_LOGGED_IN, constants.STATE_PENDING_VERIFY),
                "login_time": self._login_time.isoformat() if self._login_time else None,
                "username": self._username,
                "device_id": device_id or "",
                "devices": [d.to_api_dict() for d in self._devices],
                "state": self._state,
            }

    # ---------- 播报编排（仅云端通道：MIoT 原生播报 / mibrain 回退） ----------

    def alarm_and_speak(self, text) -> bool:
        return self.speak_with_result(text)[0]

    def speak_with_result(self, text):
        """二维返回契约：(ok, message)。"""
        ok, msg, _ = self.speak_with_detail(text)
        return ok, msg

    def speak_with_detail(self, text):
        """云端播报唯一实现路径：返回 (ok, msg, detail)。

        主通道 = MIoT 原生文字播报（文字直出、播完自动停止）；
        失败回退 mibrain 原生 TTS（同为云端通道，对 L05B/L05C 可能被服务端
        静默忽略，会附型号警示）。ok ⟺ grade ∈ {1, 2}。
        """
        from . import diagnostics

        warnings = []
        try:
            with self._lock:
                if (self._state != constants.STATE_LOGGED_IN
                        or self._cred is None or not self._cred.is_usable()):
                    logger.warning("小爱音箱未登录，跳过播报")
                    return False, "小爱音箱未登录", self._detail(
                        3, "none", "小爱音箱未登录", warnings, None, None)
                cred = self._cred
                device_id = self._resolve_device_locked()
            if not device_id:
                logger.warning("未找到可用的小爱音箱设备，跳过播报")
                return False, "未找到可用的音箱设备", self._detail(
                    3, "none", "未找到可用的音箱设备", warnings, None, None)
            hardware = self._hardware_of(device_id)
            name = self._device_name_of(device_id) or ""
            device_is_speaker = is_speaker_hardware(hardware or "", name)
            if not device_is_speaker:
                warnings.append("目标设备疑似非音箱（hardware=%s），播报可能无声"
                                % (hardware if hardware is not None else "未知"))
            ok, msg, info = self._speak_cloud(cred, device_id, PROMPT_TEXT, text)
            info = dict(info or {})
            channel = str(info.get("channel") or "none")
            if channel == "mibrain" and hardware in USE_PLAY_MUSIC_HARDWARE:
                warnings.append("该通道对此型号被服务端静默忽略（可能无声）")
            warnings = warnings + list(info.get("warnings") or [])
            grade = int(info.get("grade")
                        or self._grade_from_observation(
                            ok, info.get("observation")))
            if grade in (1, 2):
                ok = True
                msg = ("播报成功(已确认播放)" if grade == 1
                       else "指令已接受未确认播放")
            else:
                ok = False
            detail = self._detail(grade, channel, msg, warnings,
                                  (device_id, name, hardware, device_is_speaker),
                                  info.get("observation"))
            diagnostics.record_broadcast(detail)
            self._log_broadcast(grade, channel, text, detail)
            return ok, msg, detail
        except Exception as e:
            logger.error("小爱播报异常: %s", e)
            detail = self._detail(3, "none", f"播报异常: {e}", warnings,
                                  None, None)
            diagnostics.record_broadcast(detail)
            return False, f"播报异常: {e}", detail

    def _miot_did_of(self, device_id):
        """MiNA deviceID → MIoT did（device_list 的 miotDID 字段）。"""
        for d in self._devices:
            if d.device_id == device_id:
                return d.miot_did or ""
        return ""

    def _miot_observe_playing(self, cred, device_id, timeout=1.2,
                              interval=0.25):
        """原生播报后的播放状态观察，返回真实观察要素。

        - saw_playing: 是否读到 MIoT 播放状态=1（Playing）
        - last_state: 末次可读状态（"playing"/其它，读不到为 None）
        - poll_count: 真实轮询次数（命中即停）
        - note: 状态读取失败时的说明（读取失败不计入播放判断）
        """
        observation = {"saw_playing": False, "last_state": None,
                       "poll_count": 0, "volume": None}
        did = self._miot_did_of(device_id)
        if not did:
            return observation
        deadline = time.monotonic() + timeout
        errors = 0
        while True:
            observation["poll_count"] += 1
            try:
                state = self._miot.playing_state(cred, did)
                if state == constants.MIOT_PLAYING:
                    observation["saw_playing"] = True
                    observation["last_state"] = "playing"
                    return observation
                if state is not None:
                    observation["last_state"] = str(state)
            except Exception:
                errors += 1
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if errors:
                    observation["note"] = f"播放状态读取失败 {errors} 次"
                return observation
            time.sleep(min(interval, remaining))

    def _speak_native(self, cred, device_id, prompt, text):
        """MIoT 原生文字播报：提示语 → 4 秒 → 正文（两段 play-text）。

        文字直出由小米 IoT 云执行，无需本地合成/音频拉流/静音垫底；
        播报结束设备自动停止，无需 pause 收尾。
        """
        did = self._miot_did_of(device_id)
        if not did:
            raise XiaoAIError("设备缺少 miotDID，无法使用原生播报")
        started = time.monotonic()
        self._miot.play_text(cred, did, prompt)
        observation = self._miot_observe_playing(cred, device_id, timeout=1.2)
        remaining = max(0.0, 4.0 - (time.monotonic() - started))
        if remaining:
            time.sleep(remaining)
        self._miot.play_text(cred, did, text)
        grade = 1 if observation["saw_playing"] else 2
        return True, "ok", {
            "channel": "miot", "grade": grade,
            "observation": observation,
        }

    def _speak_cloud(self, cred, device_id, prompt, text):
        """云端主通道 MIoT 原生播报；失败回退 mibrain 原生 TTS（均为云端）。"""
        fallback_reason = None
        try:
            return self._speak_native(cred, device_id, prompt, text)
        except RequestTimeoutError:
            logger.warning("云端播报请求超时，结果未知（不重发不回退）")
            return False, "播报结果未知，请人工确认音箱状态", {
                "channel": "miot", "grade": 3}
        except CredentialRejectedError:
            with self._lock:
                self._enter_rejected_locked()
            logger.warning("MIoT 凭证已失效，终止播报")
            return False, "登录凭证已失效，请重新登录", {
                "channel": "miot", "grade": 3}
        except RateLimitedError:
            logger.warning("MIoT 限频，放弃本次播报")
            return False, "操作过于频繁，播报放弃", {
                "channel": "miot", "grade": 3}
        except Exception as e:
            logger.warning("MIoT 原生播报失败，回退 mibrain 原生 TTS: %s", e)
            fallback_reason = f"MIoT 原生播报失败（{e}），已回退 mibrain 原生 TTS"
        # 回退通道：mibrain 原生 TTS 两段（提示语 → 4 秒 → 正文）
        ok, msg, info = self._mibrain_speak(cred, device_id, prompt)
        if not ok:
            return False, msg, self._merged_fallback_info(info, fallback_reason)
        time.sleep(4)
        ok, msg, info = self._mibrain_speak(cred, device_id, text)
        if ok:
            observation = self._weak_observation(cred, device_id)
            info = dict(info or {})
            info["observation"] = observation
            info["grade"] = self._grade_from_observation(True, observation)
        return ok, msg, self._merged_fallback_info(info, fallback_reason)

    def _mibrain_speak(self, cred, device_id, text):
        """mibrain 原生 TTS 单段（云端；对 L05B/L05C 可能被服务端静默忽略）。"""
        try:
            self._mina.text_to_speech(cred, device_id, text)
            return True, "ok", {"channel": "mibrain"}
        except RequestTimeoutError:
            logger.warning("播报请求超时，结果未知（不重发不回退）: %s",
                           str(text)[:50])
            return False, "播报结果未知，请人工确认音箱状态", {
                "channel": "mibrain", "grade": 3}
        except CredentialRejectedError:
            with self._lock:
                self._enter_rejected_locked()
            logger.warning("凭证已失效，终止播报")
            return False, "登录凭证已失效，请重新登录", {
                "channel": "mibrain", "grade": 3}
        except RateLimitedError:
            logger.warning("限频，放弃本次播报")
            return False, "操作过于频繁，播报放弃", {
                "channel": "mibrain", "grade": 3}
        except CommandRejectedError as e:
            logger.warning("mibrain 原生 TTS 被明确拒绝: %s", e)
            return False, f"音箱未接受指令: {e}", {
                "channel": "mibrain", "grade": 3}
        except Exception as e:
            logger.error("播报失败: %s", e)
            return False, f"播报失败: {e}", {"channel": "mibrain", "grade": 3}

    def stop_playback(self):
        """立即停止当前云端播报：返回 (ok, message)。"""
        with self._lock:
            if (self._state != constants.STATE_LOGGED_IN
                    or self._cred is None or not self._cred.is_usable()):
                return False, "小爱音箱未登录，无法停止播放"
            cred = self._cred
            device_id = self._resolve_device_locked()
        if not device_id:
            return False, "未找到可用的小爱音箱设备，无法停止播放"
        did = self._miot_did_of(device_id)
        if not did:
            return False, "设备缺少 MIoT 标识，无法停止播放"
        try:
            self._miot.stop(cred, did)
            logger.info("已发送停止播放指令（MIoT）")
            return True, "已发送停止播放指令"
        except Exception as e:
            logger.warning("停止播放失败: %s", e)
            return False, f"停止播放失败: {e}"

    def _hardware_of(self, device_id):
        for d in self._devices:
            if d.device_id == device_id:
                return d.hardware
        return None

    def _device_name_of(self, device_id):
        for d in self._devices:
            if d.device_id == device_id:
                return d.name
        return None

    def _resolve_device_locked(self):
        """默认设备解析：已选设备 → 第一台音箱 → 第一台设备 → 失败（须持锁调用）。"""
        if self._device_id_cache:
            return self._device_id_cache
        if self._cred and self._cred.default_device_id:
            return self._cred.default_device_id
        for d in self._devices:
            if d.device_id and is_speaker_hardware(d.hardware, d.name):
                return d.device_id
        for d in self._devices:
            if d.device_id:
                return d.device_id
        return None

    @staticmethod
    def _grade_from_observation(accepted, observation) -> int:
        """播报分级判定（Q5 三级）：受理 + 见过 playing → 1；受理 + 无正向
        观察/观察不可用 → 2；未受理（拒绝/超时/限频/凭证失效）→ 3。"""
        if not accepted:
            return 3
        if observation and observation.get("saw_playing"):
            return 1
        return 2

    def _weak_observation(self, cred, device_id):
        """mibrain 回退播报后的单次弱观察（异常全吞，不参与控制流）。"""
        try:
            st = self._mina.get_play_status(cred, device_id)
        except Exception:
            return {"saw_playing": False, "last_state": None, "poll_count": 0,
                    "volume": None, "note": "观察不可用"}
        return {"saw_playing": st.state == "playing",
                "last_state": st.state or None, "poll_count": 1,
                "volume": st.volume}

    @staticmethod
    def _merged_fallback_info(info, fallback_reason):
        """回退路径合并降级事实/原因。"""
        info = dict(info or {})
        if fallback_reason:
            info["warnings"] = list(info.get("warnings") or []) + [fallback_reason]
        return info

    @staticmethod
    def _detail(grade, channel, message, warnings, device, observation):
        """诊断 detail 构造（timestamp 由落档补默认）。"""
        device_id, name, hardware, is_speaker = device or ("", "", None, None)
        return {
            "grade": int(grade),
            "grade_label": _GRADE_LABELS.get(int(grade), "播报失败"),
            "channel": channel,
            "message": message,
            "warnings": list(warnings or []),
            "device": {"device_id": device_id or "", "name": name or "",
                       "hardware": hardware, "is_speaker": is_speaker},
            "observation": dict(observation or {}),
        }

    @staticmethod
    def _log_broadcast(grade, channel, text, detail):
        """结构化分级日志（spec 4.4 条 1）：单条含设备三要素/通道/合成结果
        与耗时/音频时长/观察四要素/分级/警示（硬约束 8 措辞分级）。"""
        prefix = {1: "小爱播报成功(已确认播放)",
                  2: "指令已接受未确认播放"}.get(grade, "播报失败")
        device = detail["device"]
        logger.info(
            "%s: %s | 通道=%s 设备=%s(%s,%s) 观察=%s 警示=%s",
            prefix, str(text)[:80], channel,
            device["device_id"], device["name"], device["hardware"] or "未知",
            detail["observation"], detail["warnings"])

    # ---------- 课程提醒（scheduler.py 契约） ----------

    def remind_morning(self, courses) -> bool:
        try:
            if not courses:
                return self.alarm_and_speak("今天上午没有课程安排。")
            return self.alarm_and_speak(self._build_course_message(courses, "上午"))
        except Exception as e:
            logger.error("上午课程提醒播报异常: %s", e)
            return False

    def remind_afternoon(self, courses) -> bool:
        try:
            if not courses:
                return self.alarm_and_speak("今天下午没有课程安排。")
            return self.alarm_and_speak(self._build_course_message(courses, "下午"))
        except Exception as e:
            logger.error("下午课程提醒播报异常: %s", e)
            return False

    @staticmethod
    def _build_course_message(courses, period: str) -> str:
        details = []
        for c in courses:
            name = c.get("name", "未知课程")
            classroom = c.get("classroom", "")
            time_str = (c.get("start_time") or "")[:5]
            if classroom:
                details.append(f"{time_str} {name}，教室{classroom}")
            else:
                details.append(f"{time_str} {name}")
        return f"今天{period}的课程有：{'；'.join(details)}。请准备上课。"


    def _commit_credentials(self, candidate: Credentials, devices, username=None) -> None:
        """校验通过后的提交：覆盖凭证文件并迁移登录态（失败路径不会进入此处）。"""
        with self._lock:
            old_default = None
            if self._cred is not None:
                old_default = self._cred.default_device_id
            candidate.default_device_id = old_default or self._device_id_cache
            candidate.rejected = False
            self._store.save(candidate)
            self._cred = candidate
            self._devices = devices or []
            self._state = constants.STATE_LOGGED_IN
            self._username = username or str(candidate.user_id)
            self._login_time = candidate.refreshed_at
            if candidate.default_device_id:
                self._device_id_cache = candidate.default_device_id

    # ---------- 扫码登录门面方法 ----------

    def start_qr_login(self) -> dict:
        try:
            session = self._qr.start()
        except Exception as e:
            logger.error("申请登录二维码失败: %s", e)
            return {"success": False, "message": "无法连接小米服务，请稍后重试"}
        try:
            qr_image = render_qr_png_data_uri(session.login_url)
        except Exception as e:
            logger.error("二维码图片生成失败: %s", e)
            return {"success": False, "message": "二维码生成失败，请稍后重试"}
        return {
            "success": True,
            "session_id": session.session_id,
            "qr_url": session.login_url,
            "qr_image": qr_image,
            "expires_in": constants.QR_SESSION_TTL_SECONDS,
        }

    def get_qr_status(self, session_id) -> dict:
        return self._qr.get_status(session_id)

    def get_login_state(self) -> dict:
        state = self._qr.get_login_state()
        state["otp_method"] = None
        return state

    def _on_qr_confirmed(self, token: dict) -> list:
        """扫码确认回调：保存凭证 + 拉取设备列表（由 QRLoginManager 调用）。"""
        cred = Credentials(
            user_id=token["user_id"],
            pass_token=token["pass_token"],
            device_id=token["device_id"],
            service_token=token["service_token"],
            security=token.get("security"),
            refreshed_at=datetime.now(),
        )
        try:
            devices = self._mina.device_list(cred)
        except Exception as e:
            logger.error("扫码登录后拉取设备列表失败: %s", e)
            devices = []
        self._commit_credentials(cred, devices)
        return [d.to_api_dict() for d in devices]