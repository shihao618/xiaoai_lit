"""xiaoai 蓝图契约测试（N5，xiaoai_silent_play 组 5.5）：/broadcasts 四顶层键、
/test 9 键分级、/select_device 条件 warning、/devices 5 键（Flask test_client
仿 test_status_keys.py 范式，speaker 用 MagicMock，不真起 Flask）。"""
from collections import deque
from unittest.mock import MagicMock

import pytest
from flask import Flask

from lit.web.blueprints.xiaoai import create_xiaoai_blueprint
from lit.xiaoai import diagnostics


@pytest.fixture(autouse=True)
def _isolate_diagnostics(monkeypatch):
    monkeypatch.setattr(diagnostics, "_records", deque(maxlen=10))
    monkeypatch.setattr(diagnostics, "_self_check",
                        {"status": "unknown", "checked_at": None, "message": None})
    monkeypatch.setattr(diagnostics, "_self_check_done", False)


def make_client(speaker):
    app = Flask(__name__)
    app.register_blueprint(create_xiaoai_blueprint(speaker))
    return app.test_client()


DETAIL = {
    "grade": 2, "grade_label": "指令已接受未确认播放",
    "channel": "mibrain", "message": "指令已接受未确认播放",
    "warnings": ["w"],
    "device": {"device_id": "d1", "name": "小爱音箱",
               "hardware": "LX06", "is_speaker": True},
    "synth": {"ok": True, "elapsed": 0.5, "duration": 10.0, "audio": "j.mp3"},
    "observation": {"saw_playing": False, "last_state": "paused",
                    "poll_count": 2, "volume": 50},
}

TEST_KEYS = {"success", "message", "grade", "grade_label", "channel",
             "warnings", "device", "synth", "observation"}


def test_broadcasts_empty_shape():
    tc = make_client(MagicMock())
    body = tc.get("/api/xiaoai/broadcasts").get_json()
    assert set(body) == {"success", "count", "broadcasts", "audio_self_check"}
    assert body["success"] is True
    assert body["count"] == 0
    assert body["broadcasts"] == []
    assert body["audio_self_check"]["status"] == "unknown"


def test_broadcasts_latest_first():
    diagnostics.record_broadcast({"grade": 1, "channel": "primary",
                                  "message": "first"})
    diagnostics.record_broadcast({"grade": 2, "channel": "mibrain",
                                  "message": "second"})
    tc = make_client(MagicMock())
    body = tc.get("/api/xiaoai/broadcasts").get_json()
    assert body["count"] == 2
    assert body["broadcasts"][0]["message"] == "second"
    assert body["broadcasts"][1]["message"] == "first"


def test_test_endpoint_nine_keys():
    speaker = MagicMock()
    speaker.speak_with_detail.return_value = (True, "播报成功(已确认播放)", DETAIL)
    tc = make_client(speaker)
    body = tc.post("/api/xiaoai/test", json={"text": "上课"}).get_json()
    assert set(body) == TEST_KEYS
    assert body["success"] is True
    assert body["grade"] == 2
    assert body["grade_label"] == "指令已接受未确认播放"
    assert body["channel"] == "mibrain"
    assert body["warnings"] == ["w"]
    assert body["synth"]["ok"] is True


def test_test_endpoint_url_only_detail_keys():
    speaker = MagicMock()
    speaker.play_url_direct.return_value = (
        True, "指令已接受未确认播放",
        {"channel": "backup", "grade": 2,
         "observation": {"saw_playing": False, "last_state": "paused",
                         "poll_count": 3, "volume": 40}})
    tc = make_client(speaker)
    body = tc.post("/api/xiaoai/test",
                   json={"url": "http://x/a.mp3"}).get_json()
    assert set(body) == TEST_KEYS
    assert body["channel"] == "backup"
    assert body["grade"] == 2
    assert body["synth"] == {}
    speaker.play_url_direct.assert_called_once_with("http://x/a.mp3")


def test_select_device_non_speaker_warning():
    speaker = MagicMock()
    speaker.select_device.return_value = {
        "device_id": "d2", "name": "台灯", "hardware": "MJCTD01",
        "is_speaker": False, "warning": "该设备疑似非物理音箱"}
    tc = make_client(speaker)
    body = tc.post("/api/xiaoai/select_device",
                   json={"device_id": "d2"}).get_json()
    assert body["success"] is True
    assert "warning" in body
    assert "非物理音箱" in body["warning"]
    assert body["device"]["is_speaker"] is False


def test_select_device_speaker_no_warning_key():
    speaker = MagicMock()
    speaker.select_device.return_value = {
        "device_id": "d1", "name": "小爱音箱", "hardware": "LX06",
        "is_speaker": True, "warning": None}
    tc = make_client(speaker)
    body = tc.post("/api/xiaoai/select_device",
                   json={"device_id": "d1"}).get_json()
    assert body["success"] is True
    assert "warning" not in body
    assert body["device"]["is_speaker"] is True


def test_devices_five_keys():
    speaker = MagicMock()
    speaker.get_devices.return_value = [
        {"deviceID": "d1", "name": "小爱音箱", "hardware": "LX06",
         "address": "1.1.1.1", "is_speaker": True},
        {"deviceID": "d2", "name": "台灯", "hardware": "MJCTD01",
         "address": "2.2.2.2", "is_speaker": False},
    ]
    tc = make_client(speaker)
    body = tc.get("/api/xiaoai/devices").get_json()
    assert body["success"] is True
    assert len(body["devices"]) == 2
    for d in body["devices"]:
        assert set(d) == {"deviceID", "name", "hardware", "address",
                          "is_speaker"}
    assert body["devices"][0]["is_speaker"] is True
    assert body["devices"][1]["is_speaker"] is False