"""MiNA 播放协议（设备列表 / TTS / URL 播放 / 状态查询，固定指令无切换）。"""

import json
import logging
import random
import string
from dataclasses import dataclass
from typing import List, Optional

from . import constants
from .exceptions import (

    CredentialRejectedError,
    ResponseFormatError,
    classify_response,
)
from .httpclient import XiaomiHTTPClient

logger = logging.getLogger(__name__)

# 这些型号的 mibrain text_to_speech 会被服务端静默忽略（返回 code=0 但不播报）；
# 云端原生播报改走 MIoT（见 miot.py），本模块仅保留登录/设备/TTS 回退接口。
USE_PLAY_MUSIC_HARDWARE = frozenset({
    "LX04", "LX05", "L05B", "L05C", "L06", "L06A",
    "X08A", "X10A", "X08C", "X08E", "X8F", "X4B",
    "OH2", "OH2P", "X6A",
})


def is_speaker_hardware(hardware, name) -> bool:
    """设备是否疑似物理音箱（前后端统一判定事实源，自 speaker._is_speaker 平移）。"""
    hw = hardware or ""
    display = name or ""
    return (hw.startswith("L") or "speaker" in hw.lower() or "Sound" in hw
            or "小爱" in display or "音箱" in display)


@dataclass
class SpeakerDevice:
    device_id: str
    name: str
    hardware: str
    address: str
    miot_did: str = ""

    def to_api_dict(self) -> dict:
        return {
            "deviceID": self.device_id,
            "name": self.name,
            "hardware": self.hardware,
            "address": self.address,
            "is_speaker": is_speaker_hardware(self.hardware, self.name),
        }


@dataclass
class PlayerStatus:
    volume: Optional[int] = None
    state: str = ""


def _parse_number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def parse_play_status(value, depth: int = 0) -> PlayerStatus:
    """递归解析播放状态（移植 MiCast speaker.go parsePlayerStatus，≤6 层）。"""
    if depth > 6:
        return PlayerStatus()
    if isinstance(value, str):
        if len(value) < 65536:
            try:
                nested = json.loads(value)
            except (json.JSONDecodeError, ValueError):
                return PlayerStatus()
            return parse_play_status(nested, depth + 1)
        return PlayerStatus()
    if not isinstance(value, dict):
        return PlayerStatus()
    volume = None
    state = ""
    for key in ("volume", "volume_level", "volumeLevel"):
        n = _parse_number(value.get(key))
        if n is not None and 0 <= n <= 100:
            volume = n
            break
    for key in ("status", "play_status", "playStatus"):
        n = _parse_number(value.get(key))
        if n is not None:
            if n == 0:
                state = "stopped"
            elif n == 1:
                state = "playing"
            elif n == 2:
                state = "paused"
        elif isinstance(value.get(key), str):
            text = value[key].lower()
            if text in ("playing", "paused", "stopped"):
                state = text
        if state:
            break
    for key in ("info", "data", "play_info", "player"):
        nested = parse_play_status(value.get(key), depth + 1)
        if volume is None:
            volume = nested.volume
        if not state:
            state = nested.state
    return PlayerStatus(volume, state)


class MiNAClient:
    def __init__(self, http: Optional[XiaomiHTTPClient] = None):
        self._http = http or XiaomiHTTPClient()

    def device_list(self, cred) -> List[SpeakerDevice]:
        payload = self._mina_request(cred, "/admin/v2/device_list",
                                     params={"master": "0"})
        data = payload.get("data")
        if not isinstance(data, list):
            raise ResponseFormatError("音箱列表格式无法识别")
        devices = []
        for item in data:
            if not isinstance(item, dict):
                continue
            devices.append(SpeakerDevice(
                device_id=str(item.get("deviceID", "") or ""),
                name=str(item.get("name", "") or ""),
                hardware=str(item.get("hardware", "") or ""),
                address=str(item.get("address", "") or ""),
                miot_did=str(item.get("miotDID", "") or ""),
            ))
        return devices

    def text_to_speech(self, cred, device_id: str, text: str) -> None:
        """TTS 播报：path=mibrain（实测可用组合；L05B/L05C 可能被服务端静默忽略）。"""
        self._mina_request(cred, "/remote/ubus", form={
            "deviceId": device_id,
            "message": json.dumps({"text": text}, ensure_ascii=False),
            "method": "text_to_speech",
            "path": "mibrain",
        })

    def get_play_status(self, cred, device_id: str) -> PlayerStatus:
        payload = self._mina_request(cred, "/remote/ubus", form={
            "deviceId": device_id,
            "message": "{}",
            "method": "player_get_play_status",
            "path": "mediaplayer",
        })
        status = parse_play_status(payload.get("data"), 0)
        if status.volume is None and not status.state:
            raise ResponseFormatError("音箱未返回可识别的状态")
        return status

    def _mina_request(self, cred, path: str, *, params=None, form=None) -> dict:
        if cred is None or not getattr(cred, "user_id", "") \
                or not getattr(cred, "service_token", ""):
            raise CredentialRejectedError("小米账号凭证缺失")
        request_id = constants.REQUEST_ID_PREFIX + "".join(
            random.choices(string.ascii_letters + string.digits,
                           k=constants.REQUEST_ID_RANDOM_LENGTH)
        )
        cookies = {"userId": cred.user_id, "serviceToken": cred.service_token}
        mina_headers = {"User-Agent": constants.MINA_USER_AGENT}
        if form is not None:
            form = dict(form)
            form["requestId"] = request_id
            payload, _ = self._http.request(
                "POST", constants.MINA_BASE_URL + path,
                form=form, cookies=cookies, timeout=constants.MINA_TIMEOUT,
                headers=mina_headers,
            )
        else:
            params = dict(params or {})
            params["requestId"] = request_id
            payload, _ = self._http.request(
                "GET", constants.MINA_BASE_URL + path,
                params=params, cookies=cookies, timeout=constants.MINA_TIMEOUT,
                headers=mina_headers,
            )
        classify_response(200, payload)
        return payload