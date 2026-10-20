"""播报诊断记录（内存态不落盘）+ 音频服务自检（xiaoai_silent_play）。

诊断环形缓冲仅保留最近 10 次播报全要素（设备/通道/合成/观察/分级），
供 /api/xiaoai/broadcasts 只读呈现；重启归零，日志兜底全要素。
字段集为封闭白名单，结构性禁止凭证类字段进入。
"""

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime

import requests

logger = logging.getLogger(__name__)

_records = deque(maxlen=10)
_records_lock = threading.RLock()
_self_check = {"status": "unknown", "checked_at": None, "message": None}
_self_check_done = False


@dataclass
class BroadcastRecord:
    """单次播报全要素（字段集封闭白名单，无凭证类字段）。"""

    timestamp: str
    grade: int
    grade_label: str
    channel: str
    message: str
    warnings: list
    device: dict
    synth: dict
    observation: dict

    def to_api_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "grade": self.grade,
            "grade_label": self.grade_label,
            "channel": self.channel,
            "message": self.message,
            "warnings": list(self.warnings),
            "device": dict(self.device),
            "synth": dict(self.synth),
            "observation": dict(self.observation),
        }


def record_broadcast(detail: dict) -> None:
    """记录一次播报（成功/失败均入队，环形淘汰最旧）。"""
    record = BroadcastRecord(
        timestamp=str(detail.get("timestamp") or datetime.now().isoformat()),
        grade=int(detail.get("grade") or 3),
        grade_label=str(detail.get("grade_label") or ""),
        channel=str(detail.get("channel") or ""),
        message=str(detail.get("message") or ""),
        warnings=list(detail.get("warnings") or []),
        device=dict(detail.get("device") or {}),
        synth=dict(detail.get("synth") or {}),
        observation=dict(detail.get("observation") or {}),
    )
    with _records_lock:
        _records.append(record)


def get_broadcasts() -> list:
    """最近播报快照（时间倒序，最新在前）。"""
    with _records_lock:
        return [r.to_api_dict() for r in reversed(_records)]


def self_check_status() -> dict:
    """音频服务自检状态快照。"""
    with _records_lock:
        return dict(_self_check)


def run_audio_self_check() -> None:
    """音频服务自检（daemon 线程目标）：延迟 3s 等待 Flask 就绪后，
    经既有 audio 路由拉取静音文件，验证主通道音频可达性。
    至多 3 次尝试（间隔 5s）；成功后进程内不再自检；全部失败仅告警不阻断。
    """
    time.sleep(3)
    global _self_check_done
    with _records_lock:
        if _self_check_done:
            return
    from lit.web.app import APP_PORT
    from . import tts

    last_error = None
    for attempt in range(3):
        if attempt:
            time.sleep(5)
        try:
            resp = requests.get(
                "http://127.0.0.1:{}/audio/tts/{}".format(
                    APP_PORT, tts.silence_file()),
                timeout=5)
            if resp.status_code == 200 and resp.content:
                with _records_lock:
                    _self_check.update(
                        status="ok",
                        checked_at=datetime.now().isoformat(),
                        message=None)
                    _self_check_done = True
                logger.info("音频服务自检通过（主通道音频可拉流）")
                return
            last_error = "HTTP {}{}".format(
                resp.status_code, "" if resp.content else "，响应体为空")
        except Exception as e:
            last_error = str(e)
    with _records_lock:
        _self_check.update(
            status="failed",
            checked_at=datetime.now().isoformat(),
            message=last_error)
    logger.warning("音频服务自检失败，主通道播报可能无声: %s", last_error)