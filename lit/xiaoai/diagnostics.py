"""播报诊断记录（内存态不落盘，xiaoai_silent_play）。

诊断环形缓冲仅保留最近 10 次播报全要素（设备/通道/观察/分级），
供 /api/xiaoai/broadcasts 只读呈现；重启归零，日志兜底全要素。
字段集为封闭白名单，结构性禁止凭证类字段进入。
"""

import logging
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)

_records = deque(maxlen=10)
_records_lock = threading.RLock()


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
        observation=dict(detail.get("observation") or {}),
    )
    with _records_lock:
        _records.append(record)


def get_broadcasts() -> list:
    """最近播报快照（时间倒序，最新在前）。"""
    with _records_lock:
        return [r.to_api_dict() for r in reversed(_records)]
