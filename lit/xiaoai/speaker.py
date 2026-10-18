"""XiaoAISpeaker 门面：凭证四态状态机、存量接口兼容、播报编排（TTS→URL 回退）。"""

import json
import logging
import threading
import time
from datetime import datetime
from typing import List, Optional

from lit import config

from . import constants
from .account import XiaomiAccountClient, login_device_id
from .credentials import CredentialStore, Credentials
from .exceptions import (
    CommandRejectedError,
    CredentialRejectedError,
    RateLimitedError,
    RequestTimeoutError,
    XiaoAIError,
)
from .httpclient import XiaomiHTTPClient
from .mina import MiNAClient, SpeakerDevice
from .qrlogin import QRLoginManager, render_qr_png_data_uri

logger = logging.getLogger(__name__)

PROMPT_TEXT = "叮叮叮叮！上课提醒！"


class XiaoAISpeaker:
    """对 app.py / scheduler.py / cookie_manager.py 保持存量契约的门面。"""

    def __init__(self, *, http=None, account=None, mina=None, store=None, qr=None):
        self._lock = threading.RLock()
        self._store = store or CredentialStore()
        self._account = account or XiaomiAccountClient(http or XiaomiHTTPClient())
        self._mina = mina or MiNAClient(http or XiaomiHTTPClient())
        self._qr = qr or QRLoginManager(self._account, self._on_qr_confirmed)
        self._cred: Optional[Credentials] = None
        self._state = constants.STATE_NO_CREDENTIAL
        self._devices: List[SpeakerDevice] = []
        self._username = ""
        self._login_time = None
        self._device_id_cache = None
        self._load_credentials_on_init()

    # ---------- 存量兼容属性（app.py 直接读取） ----------

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

    # ---------- 存量兼容方法 ----------

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

    def select_device(self, device_id) -> None:
        with self._lock:
            self._device_id_cache = device_id
            if self._cred is not None:
                self._cred.default_device_id = device_id
                try:
                    self._store.save(self._cred)
                except Exception as e:
                    logger.warning("默认设备持久化失败: %s", e)
        logger.info("已选择小爱设备: %s", device_id)

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

    # ---------- 播报编排 ----------

    def alarm_and_speak(self, text, backup_url=None) -> bool:
        return self.speak_with_result(text, backup_url)[0]

    def speak_with_result(self, text, backup_url=None):
        backup = backup_url or (getattr(config, "XIAOAI_TTS_BACKUP_URL", "") or "")
        try:
            with self._lock:
                if (self._state != constants.STATE_LOGGED_IN
                        or self._cred is None or not self._cred.is_usable()):
                    logger.warning("小爱音箱未登录，跳过播报")
                    return False, "小爱音箱未登录"
                cred = self._cred
                device_id = self._resolve_device_locked()
            if not device_id:
                logger.warning("未找到可用的小爱音箱设备，跳过播报")
                return False, "未找到可用的音箱设备"
            ok, msg = self._speak_segment(cred, device_id, PROMPT_TEXT, text,
                                          backup)
            if ok:
                logger.info("小爱播报成功: %s", str(text)[:80])
                return True, "播报成功"
            return False, msg
        except Exception as e:
            logger.error("小爱播报异常: %s", e)
            return False, f"播报异常: {e}"

    def play_url_direct(self, url):
        try:
            with self._lock:
                if (self._state != constants.STATE_LOGGED_IN
                        or self._cred is None or not self._cred.is_usable()):
                    return False, "小爱音箱未登录"
                cred = self._cred
                device_id = self._resolve_device_locked()
            if not device_id:
                return False, "未找到可用的音箱设备"
            self._mina.play_url(cred, device_id, url,
                                self._hardware_of(device_id),
                                tail_url=self._tail_url_safe())
            # 备份音频时长未知：等待上限内轮询，到时 pause 防 L05B 列表循环
            self._wait_playback(cred, device_id,
                                constants.PLAYBACK_WAIT_TIMEOUT)
            return True, "播放成功"
        except RequestTimeoutError:
            logger.warning("备份通道播放超时，结果未知（不重发）")
            return False, "播报结果未知，请人工确认音箱状态"
        except Exception as e:
            self._apply_error(e)
            return False, f"播放失败: {e}"

    def _speak_segment(self, cred, device_id, prompt, text, backup_url):
        # 主通道：本地 TTS 合成单文件（提示语 + 4 秒静音 + 正文）+
        # player_play_music 一次播放（L05B/L05C 等型号的 mibrain
        # text_to_speech 会被服务端静默忽略，必须走音频 URL）
        if getattr(config, "XIAOAI_TTS_BASE_URL", ""):
            from . import tts as tts_module
            audio, duration = tts_module.synthesize_announcement(prompt, text)
            if audio:
                url = tts_module.audio_url(audio)
                try:
                    tail = tts_module.silence_url()
                except Exception:
                    tail = None
                try:
                    self._mina.play_url(
                        cred, device_id, url, self._hardware_of(device_id),
                        tail_url=tail)
                    self._wait_playback(cred, device_id, duration)
                    return True, "ok"
                except RequestTimeoutError:
                    logger.warning("播报请求超时，结果未知（不重发不回退）: %s",
                                   str(text)[:50])
                    return False, "播报结果未知，请人工确认音箱状态"
                except CredentialRejectedError:
                    with self._lock:
                        self._enter_rejected_locked()
                    logger.warning("凭证已失效，终止播报")
                    return False, "登录凭证已失效，请重新登录"
                except RateLimitedError:
                    logger.warning("限频，放弃本次播报")
                    return False, "操作过于频繁，播报放弃"
                except CommandRejectedError as e:
                    if backup_url:
                        logger.warning("播放被明确拒绝（%s），回退备份音频 URL", e)
                        return self._play_backup(cred, device_id, backup_url)
                    logger.warning("播放被明确拒绝且无备份通道: %s", e)
                    return False, f"音箱未接受指令: {e}"
                except Exception as e:
                    logger.warning("本地音频播报通道失败，尝试原生TTS: %s", e)
            else:
                logger.warning("本地TTS合成失败，尝试原生TTS")
        # 回退通道：mibrain text_to_speech 两段（提示语 → 4 秒 → 正文）
        ok, msg = self._mibrain_speak(cred, device_id, prompt, backup_url)
        if not ok:
            return False, msg
        time.sleep(4)
        return self._mibrain_speak(cred, device_id, text, backup_url)

    def _mibrain_speak(self, cred, device_id, text, backup_url):
        """mibrain TTS 单段播报（对 L05B/L05C 无效，其他型号可用）。"""
        try:
            self._mina.text_to_speech(cred, device_id, text)
            return True, "ok"
        except RequestTimeoutError:
            logger.warning("播报请求超时，结果未知（不重发不回退）: %s",
                           str(text)[:50])
            return False, "播报结果未知，请人工确认音箱状态"
        except CredentialRejectedError:
            with self._lock:
                self._enter_rejected_locked()
            logger.warning("凭证已失效，终止播报")
            return False, "登录凭证已失效，请重新登录"
        except RateLimitedError:
            logger.warning("限频，放弃本次播报")
            return False, "操作过于频繁，播报放弃"
        except CommandRejectedError as e:
            if backup_url:
                logger.warning("TTS 被明确拒绝（%s），回退备份音频 URL", e)
                return self._play_backup(cred, device_id, backup_url)
            logger.warning("TTS 被明确拒绝且无备份通道: %s", e)
            return False, f"音箱未接受指令: {e}"
        except Exception as e:
            logger.error("播报失败: %s", e)
            return False, f"播报失败: {e}"

    def _wait_playback(self, cred, device_id, duration):
        """等待播放完成并暂停，防止 REPLACE_ALL 列表循环重播。

        L05B/L05C 的 player_get_play_status 恒返回 paused（状态不可靠）、
        action=stop 被固件忽略，故以音频时长为主钟：到时无条件发 pause
        （play_music 列表第二项为 30 秒静音，pause 落在静音段，正文恰好
        播放一遍）；期间若可靠观察到 playing → 非 playing 则提前 pause。
        """
        deadline = time.monotonic() + min(
            max(duration, 2.0) + constants.PLAYBACK_START_GRACE,
            constants.PLAYBACK_WAIT_TIMEOUT)
        saw_playing = False
        while time.monotonic() < deadline:
            time.sleep(constants.PLAYBACK_POLL_INTERVAL)
            try:
                st = self._mina.get_play_status(cred, device_id)
            except Exception:
                continue
            if st.state == "playing":
                saw_playing = True
            elif saw_playing:
                break
        try:
            self._mina.stop_playback(cred, device_id)
        except Exception:
            pass

    @staticmethod
    def _tail_url_safe():
        """静音垫底 URL（可选）：L05B/L05C 列表循环落静音段；未启用本地 TTS 返回 None。"""
        if not getattr(config, "XIAOAI_TTS_BASE_URL", ""):
            return None
        try:
            from . import tts as tts_module
            return tts_module.silence_url()
        except Exception:
            return None

    def _hardware_of(self, device_id):
        for d in self._devices:
            if d.device_id == device_id:
                return d.hardware
        return None

    def _play_backup(self, cred, device_id, url):
        try:
            self._mina.play_url(cred, device_id, url,
                                self._hardware_of(device_id),
                                tail_url=self._tail_url_safe())
            self._wait_playback(cred, device_id,
                                constants.PLAYBACK_WAIT_TIMEOUT)
            return True, "ok"
        except RequestTimeoutError:
            logger.warning("备份通道播放超时，结果未知（不重发）")
            return False, "播报结果未知，请人工确认音箱状态"
        except CredentialRejectedError:
            with self._lock:
                self._enter_rejected_locked()
            return False, "登录凭证已失效，请重新登录"
        except Exception as e:
            logger.warning("备份通道播放失败: %s", e)
            return False, f"备份通道播放失败: {e}"

    def _resolve_device_locked(self):
        """默认设备解析：已选设备 → 第一台音箱 → 第一台设备 → 失败（须持锁调用）。"""
        if self._device_id_cache:
            return self._device_id_cache
        if self._cred and self._cred.default_device_id:
            return self._cred.default_device_id
        for d in self._devices:
            if d.device_id and self._is_speaker(d):
                return d.device_id
        for d in self._devices:
            if d.device_id:
                return d.device_id
        return None

    @staticmethod
    def _is_speaker(device: SpeakerDevice) -> bool:
        hw = device.hardware or ""
        name = device.name or ""
        return (hw.startswith("L") or "speaker" in hw.lower() or "Sound" in hw
                or "小爱" in name or "音箱" in name)

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
            section = c.get("section_start", 0)
            time_str = config.COURSE_TIME_MAP.get(section, "")
            if classroom:
                details.append(f"{time_str} {name}，教室{classroom}")
            else:
                details.append(f"{time_str} {name}")
        return f"今天{period}的课程有：{'；'.join(details)}。请准备上课。"

    # ---------- 手动凭证兜底（两阶段：校验 → 提交 / 回滚） ----------

    def set_manual_cookie(self, cookie_str, username=None) -> bool:
        cookies = {}
        for pair in (cookie_str or "").split(";"):
            pair = pair.strip()
            if "=" in pair:
                k, v = pair.split("=", 1)
                cookies[k.strip()] = v.strip()
        user_id = str(cookies.get("userId", "") or "")
        service_token = str(cookies.get("serviceToken", "") or "")
        if not user_id or not service_token:
            raise XiaoAIError("Cookie格式错误，需包含 userId 与 serviceToken")
        candidate = Credentials(
            user_id=user_id,
            pass_token="",
            device_id=str(cookies.get("deviceId", "") or "") or login_device_id(user_id),
            service_token=service_token,
            refreshed_at=datetime.now(),
        )
        devices = self._mina.device_list(candidate)
        self._commit_credentials(candidate, devices, username)
        logger.info("手动设置小爱Cookie成功（校验通过）")
        return True

    def set_manual_token(self, token_str, username=None) -> bool:
        try:
            data = json.loads(token_str)
        except (json.JSONDecodeError, ValueError):
            raise XiaoAIError("Token格式错误")
        if not isinstance(data, dict):
            raise XiaoAIError("Token格式错误")
        user_id = str(data.get("userId") or data.get("user_id") or "")
        pass_token = str(data.get("passToken") or data.get("pass_token") or "")
        service_token = str(data.get("serviceToken") or data.get("service_token") or "")
        device_id = str(data.get("deviceId") or data.get("device_id") or "")
        security = data.get("ssecurity") or data.get("security") or None
        if not user_id or (not pass_token and not service_token):
            raise XiaoAIError("Token格式错误，需包含 userId 与 passToken/serviceToken")
        if not device_id:
            device_id = login_device_id(user_id)
        if pass_token and not service_token:
            token = self._account.exchange(user_id, pass_token, device_id)
            service_token = token["service_token"]
            security = token.get("security") or security
        candidate = Credentials(
            user_id=user_id,
            pass_token=pass_token,
            device_id=device_id,
            service_token=service_token,
            security=security,
            refreshed_at=datetime.now(),
        )
        devices = self._mina.device_list(candidate)
        self._commit_credentials(candidate, devices, username)
        logger.info("手动设置小爱Token成功（校验通过）")
        return True

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