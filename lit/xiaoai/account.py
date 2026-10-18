"""小米账号扫码登录协议（start_qr / poll_qr / exchange，移植 MiCast client.go）。"""

import base64
import hashlib
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlparse

import requests

from . import constants
from .exceptions import (
    CredentialRejectedError,
    RateLimitedError,
    RequestTimeoutError,
    ResponseFormatError,
)
from .httpclient import XiaomiHTTPClient, trusted_url

logger = logging.getLogger(__name__)


def payload_str(payload: dict, key: str) -> str:
    """从响应字典取字符串（兼容 JSON number，对应 Go 的 str()）。"""
    v = payload.get(key)
    if isinstance(v, str):
        return v
    if isinstance(v, bool):
        return ""
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return ""


def login_device_id(user_id: str) -> str:
    """登录设备标识：md5("lit-xiaoai-" + user_id) 十六进制前 16 位（同账号稳定）。"""
    return hashlib.md5(("lit-xiaoai-" + str(user_id)).encode("utf-8")).hexdigest()[:16]


@dataclass
class PollResult:
    status: str  # waiting / waiting_limited / expired / confirmed
    user_id: Optional[str] = None
    pass_token: Optional[str] = None
    next_poll_url: Optional[str] = None


class XiaomiAccountClient:
    def __init__(self, http: Optional[XiaomiHTTPClient] = None):
        self._http = http or XiaomiHTTPClient()

    def start_qr(self) -> Tuple[str, str, str]:
        """申请扫码登录二维码，返回 (login_url, poll_url, device_id)。"""
        device = secrets.token_hex(8)
        params = {
            "_qrsize": "480",
            "qs": "%3Fsid%3Dxiaomiio%26_json%3Dtrue",
            "callback": "https://sts.api.io.mi.com/sts",
            "_hasLogo": "false",
            "sid": "xiaomiio",
            "serviceParam": "",
            "_locale": "zh_CN",
            "_dc": str(int(time.time() * 1000)),
        }
        payload, _ = self._http.request(
            "GET",
            constants.ACCOUNT_BASE_URL + "/longPolling/loginUrl",
            params=params,
            cookies={"sdkVersion": constants.SDK_VERSION, "deviceId": device},
            timeout=constants.QR_START_TIMEOUT,
        )
        login_url = payload_str(payload, "loginUrl")
        lp = payload_str(payload, "lp")
        if not login_url or not lp or not trusted_url(login_url) or not trusted_url(lp):
            raise ResponseFormatError("未获取到有效的小米登录二维码")
        logger.info("小米登录二维码申请成功")
        return login_url, lp, device

    def poll_qr(self, poll_url: str) -> PollResult:
        """轮询扫码状态（22 秒长轮询），四态判定。"""
        if not trusted_url(poll_url):
            raise ResponseFormatError("无效的登录轮询地址")
        try:
            payload, _ = self._http.request(
                "GET", poll_url, timeout=constants.QR_POLL_TIMEOUT
            )
        except RateLimitedError:
            return PollResult("waiting_limited", next_poll_url=poll_url)
        except RequestTimeoutError:
            return PollResult("waiting", next_poll_url=poll_url)
        code_raw = payload.get("code")
        if isinstance(code_raw, int) and code_raw == constants.CODE_CREDENTIAL_EXPIRED:
            return PollResult("expired", next_poll_url=poll_url)
        if isinstance(code_raw, str):
            try:
                if int(code_raw) == constants.CODE_CREDENTIAL_EXPIRED:
                    return PollResult("expired", next_poll_url=poll_url)
            except ValueError:
                pass
        pass_token = payload_str(payload, "passToken")
        if pass_token:
            user_id = payload_str(payload, "userId")
            if not user_id:
                raise ResponseFormatError("登录结果缺少账号信息")
            return PollResult("confirmed", user_id=user_id,
                              pass_token=pass_token, next_poll_url=poll_url)
        new_lp = payload_str(payload, "lp")
        if new_lp:
            if not trusted_url(new_lp):
                raise ResponseFormatError("无效的登录轮询地址")
            return PollResult("waiting", next_poll_url=new_lp)
        return PollResult("waiting", next_poll_url=poll_url)

    def exchange(self, user_id: str, pass_token: str, device_id: str) -> dict:
        """passToken 换取 serviceToken（clientSign 签名），返回凭证字段 dict。"""
        session = requests.Session()
        try:
            cookies = {
                "sdkVersion": constants.SDK_VERSION,
                "deviceId": device_id,
                "userId": str(user_id),
                "passToken": pass_token,
            }
            payload, _ = self._http.request(
                "GET",
                constants.ACCOUNT_BASE_URL + "/pass/serviceLogin",
                params={"sid": "micoapi", "_json": "true"},
                cookies=cookies,
                timeout=constants.QR_POLL_TIMEOUT,
                session=session,
            )
            code = payload.get("code")
            if isinstance(code, str):
                try:
                    code = int(code)
                except ValueError:
                    code = None
            if code == constants.CODE_RATE_LIMITED:
                raise RateLimitedError("小米账号请求过于频繁，请稍后重试")
            if code in (constants.CODE_CREDENTIAL_EXPIRED,
                        constants.CODE_CREDENTIAL_REJECTED):
                raise CredentialRejectedError("小米账号凭证已失效，请重新登录")
            security = payload_str(payload, "ssecurity")
            nonce = payload_str(payload, "nonce")
            location = payload_str(payload, "location")
            if code != 0 or not security or not nonce or not trusted_url(location):
                raise ResponseFormatError("小米登录验证未完成，请稍后重试")

            digest = hashlib.sha1(
                ("nonce=" + nonce + "&" + security).encode("utf-8")
            ).digest()
            client_sign = base64.b64encode(digest).decode("ascii")
            final_url = self._append_query(location, {"clientSign": client_sign})

            _, resp = self._http.request(
                "GET", final_url,
                timeout=constants.QR_POLL_TIMEOUT,
                session=session,
                parse_json=False,
            )
            service_token = self._extract_service_token(session, resp, final_url)
            if not service_token:
                raise ResponseFormatError("小米未返回服务凭证")
            logger.info("小米凭证换取成功")
            return {
                "user_id": str(user_id),
                "pass_token": pass_token,
                "device_id": device_id,
                "service_token": service_token,
                "security": security,
            }
        finally:
            session.close()

    @staticmethod
    def _append_query(url: str, extra: dict) -> str:
        base, _, query = url.partition("?")
        pairs = [(k, v) for k, v in parse_qsl(query, keep_blank_values=True)
                 if k not in extra]
        pairs.extend(extra.items())
        return base + "?" + urlencode(pairs)

    @staticmethod
    def _extract_service_token(session, resp, request_url: str) -> str:
        """serviceToken 双路提取：优先响应 Set-Cookie，缺失时从 jar 按 location 域兜底。"""
        try:
            for cookie in resp.cookies:
                if cookie.name == "serviceToken" and cookie.value:
                    return cookie.value
        except Exception:
            pass
        targets = set()
        for url in (request_url, getattr(resp, "url", "")):
            if not url:
                continue
            host = (urlparse(url).hostname or "").lower()
            if host:
                targets.add(host)
        for cookie in session.cookies:
            if cookie.name != "serviceToken" or not cookie.value:
                continue
            domain = (cookie.domain or "").lstrip(".").lower()
            if not domain:
                continue
            for host in targets:
                if host == domain or host.endswith("." + domain):
                    return cookie.value
        return ""