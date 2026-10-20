"""MIoT 原生播报通道：把文字直接交给小米 IoT 云，由音箱原生播报。

背景：L05B/L05C 等型号的 MiNA `mibrain/text_to_speech` 会被服务端静默忽略，
而本地 TTS + 音频 URL 拉流又需要静音垫底与 pause 收尾。实测发现小米 IoT 云的
MIoT 动作可以原生播报文字，且设备 `playing-state` 如实变为 Playing：

  POST https://api.io.mi.com/app/miotspec/action
    {"params": {"did": "<miotDID>", "siid": 5, "aiid": 3, "in": ["文本"]}}
      siid=5 Intelligent Speaker / aiid=3 Play Text（文字直接播报）
      siid=5 / aiid=4 Execute Text Directive（模拟语音指令，in=[文本, 0|1]）

该接口属于 xiaomiio 服务，需要用扫码登录保存的 passToken 另换一份服务令牌
（与 MiNA 的 micoapi 令牌相互独立），令牌缓存到 data/xiaoai/miot_token.json。
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime
from urllib.parse import urlencode

import requests

from lit import config

from . import constants
from .exceptions import (
    CommandRejectedError,
    CredentialRejectedError,
    NetworkError,
    RequestTimeoutError,
    ResponseFormatError,
    XiaoAIError,
)

logger = logging.getLogger(__name__)

_TOKEN_FILENAME = "miot_token.json"
_ACCOUNT_BASE = constants.ACCOUNT_BASE_URL
_JSON_PREFIX = constants.JSON_PREFIX.encode("utf-8")


def parse_account_payload(raw: bytes) -> dict:
    """解析 account.xiaomi.com 响应（可能带 &&&START&&& 前缀）。"""
    raw = (raw or b"").strip()
    if raw.startswith(_JSON_PREFIX):
        raw = raw[len(_JSON_PREFIX):]
    if not raw:
        return {}
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise ResponseFormatError("小米账号响应无法解析") from e
    return payload if isinstance(payload, dict) else {}


def sign_data(uri: str, data: dict, ssecurity: str) -> dict:
    """按小米 IoT 云规则签名请求（HMAC-SHA256，nonce 含时间片）。"""
    data_str = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    nonce = base64.b64encode(
        os.urandom(8) + int(time.time() / 60).to_bytes(4, "big")).decode()
    snonce = base64.b64encode(hashlib.sha256(
        base64.b64decode(ssecurity) + base64.b64decode(nonce)).digest()).decode()
    msg = "&".join([uri, snonce, nonce, "data=" + data_str])
    signature = base64.b64encode(hmac.new(
        base64.b64decode(snonce), msg.encode("utf-8"),
        hashlib.sha256).digest()).decode()
    return {"_nonce": nonce, "data": data_str, "signature": signature}


class MiOTClient:
    """小米 IoT 云（xiaomiio）客户端：原生文字播报与播放状态查询。"""

    def __init__(self, session=None):
        self._session = session or requests.Session()
        self._token = None  # (user_id, ssecurity, service_token)

    # ---------- 令牌换取与缓存 ----------

    def _token_path(self) -> str:
        return os.path.join(config.DATA_DIR, "xiaoai", _TOKEN_FILENAME)

    def _load_cached_token(self, cred):
        try:
            with open(self._token_path(), "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return None
        if str(data.get("user_id") or "") != str(cred.user_id or ""):
            return None
        security = data.get("ssecurity") or ""
        token = data.get("service_token") or ""
        if not security or not token:
            return None
        return (str(cred.user_id), security, token)

    def _save_cached_token(self, token) -> None:
        path = self._token_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"user_id": token[0], "ssecurity": token[1],
                       "service_token": token[2],
                       "saved_at": datetime.now().isoformat(timespec="seconds")},
                      f, ensure_ascii=False, indent=2)
        if os.name != "nt":
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
        os.replace(tmp, path)

    def _get(self, url, **kwargs):
        try:
            return self._session.get(url, timeout=constants.MIOT_LOGIN_TIMEOUT,
                                     **kwargs)
        except requests.Timeout as e:
            raise RequestTimeoutError("小米账号请求超时") from e
        except requests.RequestException as e:
            raise NetworkError(f"小米账号连接失败: {e}") from e

    def _exchange_token(self, cred):
        """用扫码登录保存的 passToken 换取 xiaomiio 服务令牌。"""
        if not cred.pass_token or not cred.device_id:
            raise CredentialRejectedError("缺少小米登录凭证，请重新扫码登录")
        cookies = {
            "sdkVersion": "3.9",
            "deviceId": cred.device_id,
            "userId": str(cred.user_id),
            "passToken": cred.pass_token,
        }
        resp = self._get(_ACCOUNT_BASE + "/pass/serviceLogin",
                         params={"sid": "xiaomiio", "_json": "true"},
                         cookies=cookies)
        payload = parse_account_payload(resp.content)
        code = payload.get("code")
        if code != 0:
            raise CredentialRejectedError(
                "小米 IoT 令牌换取失败（passToken 可能已过期，请重新扫码登录）: "
                f"code={code}")
        security = payload.get("ssecurity") or ""
        nonce = payload.get("nonce")
        location = payload.get("location") or ""
        if not (security and nonce and location.startswith("https://")):
            raise ResponseFormatError("小米 IoT 登录响应缺少关键字段")

        digest = hashlib.sha1(
            ("nonce=" + str(nonce) + "&" + security).encode("utf-8")).digest()
        client_sign = base64.b64encode(digest).decode()
        sep = "&" if "?" in location else "?"
        self._get(location + sep + urlencode({"clientSign": client_sign}))
        token = ""
        try:
            for cookie in self._session.cookies:
                if cookie.name == "serviceToken" and cookie.value:
                    token = cookie.value
                    break
        except Exception:
            token = ""
        if not token:
            raise ResponseFormatError("小米未返回 IoT 服务凭证")

        self._token = (str(cred.user_id), security, token)
        try:
            self._save_cached_token(self._token)
        except OSError as e:
            logger.warning("MIoT 令牌持久化失败（仅内存可用）: %s", e)
        logger.info("MIoT 服务令牌获取成功")
        return self._token

    def ensure_token(self, cred, refresh: bool = False):
        if self._token and not refresh \
                and self._token[0] == str(cred.user_id or ""):
            return self._token
        if not refresh:
            cached = self._load_cached_token(cred)
            if cached:
                self._token = cached
                return cached
        return self._exchange_token(cred)

    # ---------- 请求 ----------

    def _call(self, cred, uri: str, params, retry: bool = True) -> dict:
        user_id, security, token = self.ensure_token(cred)
        body = sign_data(uri, params, security)
        headers = {"User-Agent": constants.MIOT_USER_AGENT,
                   "x-xiaomi-protocal-flag-cli": constants.MIOT_PROTOCOL_HEADER}
        cookies = {"userId": user_id, "serviceToken": token}
        try:
            resp = self._session.post(
                constants.MIOT_BASE_URL + uri, data=body, headers=headers,
                cookies=cookies, timeout=constants.MIOT_TIMEOUT)
        except requests.Timeout as e:
            raise RequestTimeoutError("MIoT 请求超时") from e
        except requests.RequestException as e:
            raise NetworkError(f"MIoT 连接失败: {e}") from e

        if resp.status_code == 401 and retry:
            self.ensure_token(cred, refresh=True)
            return self._call(cred, uri, params, retry=False)
        if resp.status_code == 401:
            raise CredentialRejectedError("小米 IoT 凭证已失效，请重新扫码登录")
        if resp.status_code != 200:
            raise NetworkError(f"MIoT 返回 HTTP {resp.status_code}")
        try:
            payload = resp.json()
        except ValueError as e:
            raise ResponseFormatError("MIoT 响应无法解析") from e
        if not isinstance(payload, dict):
            raise ResponseFormatError("MIoT 响应格式异常")

        code = payload.get("code")
        if code == 401:
            if retry:
                self.ensure_token(cred, refresh=True)
                return self._call(cred, uri, params, retry=False)
            raise CredentialRejectedError("小米 IoT 凭证已失效，请重新扫码登录")
        if code not in (0, None):
            raise CommandRejectedError(
                f"MIoT 拒绝请求（code={code}）: {payload.get('message') or ''}")
        result = payload.get("result")
        inner = result.get("code") if isinstance(result, dict) else None
        if inner not in (0, None):
            raise CommandRejectedError(f"MIoT 设备执行失败（code={inner}）")
        return payload

    # ---------- 业务动作 ----------

    def play_text(self, cred, miot_did: str, text: str) -> dict:
        """原生文字播报（siid=5/aiid=3 Play Text）。"""
        return self._call(cred, "/miotspec/action", {"params": {
            "did": str(miot_did),
            "siid": constants.MIOT_SPEAKER_SIID,
            "aiid": constants.MIOT_PLAY_TEXT_AIID,
            "in": [str(text)],
        }})

    def execute_text(self, cred, miot_did: str, text: str,
                     with_response: bool = True) -> dict:
        """模拟对小爱说一句话（siid=5/aiid=4 Execute Text Directive）。"""
        return self._call(cred, "/miotspec/action", {"params": {
            "did": str(miot_did),
            "siid": constants.MIOT_SPEAKER_SIID,
            "aiid": constants.MIOT_EXECUTE_TEXT_AIID,
            "in": [str(text), 1 if with_response else 0],
        }})

    def playing_state(self, cred, miot_did: str):
        """读取播放状态（siid=3/piid=1）：1=Playing；读取失败返回 None。"""
        try:
            payload = self._call(cred, "/miotspec/prop/get", {"params": [{
                "did": str(miot_did),
                "siid": constants.MIOT_PLAY_CONTROL_SIID,
                "piid": constants.MIOT_PLAYING_STATE_PIID,
            }]})
        except XiaoAIError:
            return None
        try:
            item = payload["result"][0]
            return item.get("value") if item.get("code") == 0 else None
        except (KeyError, IndexError, TypeError):
            return None

    def stop(self, cred, miot_did: str) -> dict:
        """停止当前播报：发送 Pause（siid=3/aiid=3）。

        真机实测：L05B/L05C 固件对 Play Control 的 Stop 动作返回
        -704220008（不支持），Pause 可用；原生播报不会循环，Pause 即可终止。
        """
        return self._call(cred, "/miotspec/action", {"params": {
            "did": str(miot_did),
            "siid": constants.MIOT_PLAY_CONTROL_SIID,
            "aiid": constants.MIOT_PAUSE_AIID,
            "in": [],
        }})
