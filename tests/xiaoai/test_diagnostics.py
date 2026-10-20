"""diagnostics 模块测试：环形缓冲要素、字段白名单、最新在前、无凭证字段。"""
import json
from collections import deque

import pytest

from lit.xiaoai import diagnostics


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.setattr(diagnostics, "_records", deque(maxlen=10))


def test_record_and_get_elements():
    detail = {
        "grade": 1, "grade_label": "已确认播放",
        "channel": "miot", "message": "播报成功(已确认播放)",
        "warnings": ["w1"],
        "device": {"device_id": "d1", "name": "小爱音箱",
                   "hardware": "L05B", "is_speaker": True},
        "observation": {"saw_playing": True, "last_state": "playing",
                        "poll_count": 1, "volume": None},
    }
    diagnostics.record_broadcast(detail)
    records = diagnostics.get_broadcasts()
    assert len(records) == 1
    rec = records[0]
    assert set(rec) == {"timestamp", "grade", "grade_label", "channel",
                        "message", "warnings", "device", "observation"}
    assert rec["channel"] == "miot"
    assert rec["device"]["hardware"] == "L05B"
    assert rec["observation"]["saw_playing"] is True
    assert rec["timestamp"]


def test_ring_eviction_keeps_latest_ten():
    for i in range(12):
        diagnostics.record_broadcast({"grade": 1, "channel": "miot",
                                      "message": "m%d" % i})
    records = diagnostics.get_broadcasts()
    assert len(records) == 10
    assert records[0]["message"] == "m11"  # 最新在前
    assert records[-1]["message"] == "m2"


def test_missing_fields_get_defaults():
    diagnostics.record_broadcast({})
    rec = diagnostics.get_broadcasts()[0]
    assert rec["grade"] == 3
    assert rec["channel"] == ""
    assert rec["device"] == {}
    assert rec["observation"] == {}
    assert rec["warnings"] == []


def test_api_dict_key_whitelist_no_credentials():
    diagnostics.record_broadcast({
        "grade": 1, "channel": "miot", "message": "m",
        "grade_label": "已确认播放", "timestamp": "t",
        "device": {}, "observation": {}, "warnings": [],
        "pass_token": "SECRET_PT", "service_token": "SECRET_ST",
    })
    rec = diagnostics.get_broadcasts()[0]
    blob = json.dumps(rec, ensure_ascii=False)
    for secret in ("pass_token", "service_token", "SECRET_PT", "SECRET_ST"):
        assert secret not in blob
