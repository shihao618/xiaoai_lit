"""扫码登录会话管理与后台长轮询线程 + 二维码 PNG 生成。"""

import base64
import io
import logging
import secrets
import threading
import time
from typing import Callable, Optional

import qrcode
from qrcode.image.pure import PyPNGImage

from . import constants
from .account import login_device_id
from .exceptions import (
    CredentialRejectedError,
    NetworkError,
    RateLimitedError,
    QRSessionError,
    ResponseFormatError,
)

logger = logging.getLogger(__name__)


def render_qr_png_data_uri(login_url: str) -> str:
    """将登录地址渲染为 PNG 的 Base64 Data URI（前端 <img> 直接展示）。"""
    qr = qrcode.QRCode(image_factory=PyPNGImage)
    qr.add_data(login_url)
    img = qr.make_image()
    buf = io.BytesIO()
    img.save(buf)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


class QRLoginSession:
    TERMINAL_STATUSES = ("confirmed", "expired", "failed")

    def __init__(self, session_id: str, login_url: str, poll_url: str, device_id: str):
        self.session_id = session_id
        self.login_url = login_url
        self.poll_url = poll_url
        self.device_id = device_id
        self.created_at = time.monotonic()
        self.expires_at = self.created_at + constants.QR_SESSION_TTL_SECONDS
        self.status = "created"
        self.message = "等待扫码"
        self.devices = []
        self.cancel = threading.Event()
        self._lock = threading.Lock()

    def set_status(self, status: str, message: str) -> None:
        with self._lock:
            if self.status in self.TERMINAL_STATUSES:
                return
            if self.status != status or self.message != message:
                logger.info("扫码登录状态变化: %s (%s)", status, message)
            self.status = status
            self.message = message

    def finish(self, status: str, message: str, devices=None) -> None:
        with self._lock:
            if self.status not in self.TERMINAL_STATUSES:
                logger.info("扫码登录会话结束: %s (%s)", status, message)
            self.status = status
            self.message = message
            if devices is not None:
                self.devices = devices

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "status": self.status,
                "message": self.message,
                "devices": list(self.devices),
            }


class QRLoginManager:
    """会话生命周期：start 创建新会话并作废旧会话；后台线程对小米 22 秒长轮询。

    前端状态查询（get_status / get_login_state）只读内存，毫秒级返回。
    """

    def __init__(self, account_client, on_confirmed: Callable[[dict], list]):
        self._account = account_client
        self._on_confirmed = on_confirmed
        self._lock = threading.Lock()
        self._session: Optional[QRLoginSession] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> QRLoginSession:
        login_url, poll_url, device_id = self._account.start_qr()
        session = QRLoginSession(secrets.token_hex(8), login_url, poll_url, device_id)
        with self._lock:
            old_session = self._session
            old_thread = self._thread
            self._session = session
            self._thread = None
        if old_session is not None:
            old_session.cancel.set()
        if old_thread is not None and old_thread.is_alive():
            old_thread.join(timeout=2)
        thread = threading.Thread(
            target=self._worker, args=(session,), daemon=True,
            name=f"qr-login-{session.session_id[:6]}",
        )
        with self._lock:
            self._thread = thread
        thread.start()
        return session

    def get_status(self, session_id: str) -> dict:
        """查询指定会话状态；终态会话保留可查询，直到被新会话替换。"""
        with self._lock:
            session = self._session
        if not session_id or session is None or session.session_id != session_id:
            raise QRSessionError("登录会话不存在或已结束")
        snap = session.snapshot()
        result = {"success": True, "status": snap["status"], "message": snap["message"]}
        if snap["status"] == "confirmed":
            result["devices"] = snap["devices"]
        return result

    def get_login_state(self) -> dict:
        """login_status 视图：无会话时保持上次终态。"""
        with self._lock:
            session = self._session
        if session is None:
            return {"status": "idle", "message": "", "devices": []}
        snap = session.snapshot()
        status = snap["status"]
        if status in ("created", "waiting", "exchanging"):
            return {"status": "logging_in", "message": snap["message"], "devices": []}
        if status == "waiting_limited":
            return {"status": "logging_in",
                    "message": "扫码状态查询受限，请稍候", "devices": []}
        if status == "confirmed":
            return {"status": "success", "message": "登录成功",
                    "devices": snap["devices"]}
        if status == "expired":
            return {"status": "failed",
                    "message": "二维码已过期，请刷新后重新扫码", "devices": []}
        return {"status": "failed", "message": snap["message"] or "登录失败",
                "devices": []}

    def _worker(self, session: QRLoginSession) -> None:
        poll_url = session.poll_url
        session.set_status("waiting", "等待扫码")
        while not session.cancel.is_set():
            if time.monotonic() > session.expires_at:
                session.finish("expired", "二维码已过期，请刷新后重新扫码")
                return
            try:
                result = self._account.poll_qr(poll_url)
            except CredentialRejectedError as e:
                session.finish("failed", f"凭证被拒，请重新扫码: {e}")
                return
            except (NetworkError, RateLimitedError, ResponseFormatError) as e:
                logger.warning("扫码轮询临时失败，稍后续轮: %s", e)
                session.set_status("waiting_limited", "扫码状态查询受限，请稍候")
                if session.cancel.wait(constants.QR_NETWORK_RETRY_DELAY):
                    return
                continue
            except Exception as e:
                logger.error("扫码轮询异常，会话终止: %s", e)
                session.finish("failed", f"登录异常: {e}")
                return

            if result.next_poll_url:
                poll_url = result.next_poll_url
            if result.status == "waiting":
                session.set_status("waiting", "等待扫码")
                continue
            if result.status == "waiting_limited":
                session.set_status("waiting_limited", "扫码状态查询受限，请稍候")
                if session.cancel.wait(constants.QR_LIMITED_RETRY_DELAY):
                    return
                continue
            if result.status == "expired":
                session.finish("expired", "二维码已过期，请刷新后重新扫码")
                return

            session.set_status("exchanging", "扫码成功，正在完成登录...")
            try:
                token = self._account.exchange(
                    result.user_id, result.pass_token,
                    login_device_id(result.user_id),
                )
            except RateLimitedError:
                session.finish("failed", "操作过于频繁，请稍后重试")
                return
            except CredentialRejectedError:
                session.finish("failed", "凭证无效，请重新获取")
                return
            except Exception as e:
                session.finish("failed", f"登录失败: {e}")
                return
            devices = []
            try:
                devices = self._on_confirmed(token) or []
            except Exception as e:
                logger.error("登录确认后处理失败: %s", e)
            session.finish("confirmed", "登录成功", devices=devices)
            return