"""diagnostics 模块测试（N1，xiaoai_silent_play 组 5.4）：环形缓冲要素、
键集白名单、自检状态流转（requests.get 全 mock，不真起 Flask）。"""
import json
import logging
from collections import deque

import pytest

from lit.xiaoai import diagnostics


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.setattr(diagnostics, "_records", deque(maxlen=10))
    monkeypatch.setattr(diagnostics, "_self_check",
                        {"status": "unknown", "checked_at": None, "message": None})
    monkeypatch.setattr(diagnostics, "_self_check_done", False)


class _OkResponse:
    status_code = 200
    content = b"x" * 10


def _install_self_check_mocks(monkeypatch, handler):
    calls = []

    def fake_get(url, timeout=None):
        calls.append(url)
        return handler(url)

    monkeypatch.setattr(diagnostics.requests, "get", fake_get)
    monkeypatch.setattr(diagnostics.time, "sleep", lambda s: None)
    monkeypatch.setattr("lit.xiaoai.tts.silence_file",
                        lambda: "silence30.mp3")
    return calls


def test_record_and_get_full_elements():
    detail = {
        "grade": 1, "grade_label": "已确认播放",
        "channel": "primary", "message": "播报成功(已确认播放)",
        "warnings": ["w1"],
        "device": {"device_id": "d1", "name": "小爱音箱",
                   "hardware": "LX06", "is_speaker": True},
        "synth": {"ok": True, "elapsed": 0.5, "duration": 10.0,
                  "audio": "j.mp3"},
        "observation": {"saw_playing": True, "last_state": "playing",
                        "poll_count": 3, "volume": 50},
    }
    diagnostics.record_broadcast(detail)
    records = diagnostics.get_broadcasts()
    assert len(records) == 1
    rec = records[0]
    assert set(rec) == {"timestamp", "grade", "grade_label", "channel",
                        "message", "warnings", "device", "synth",
                        "observation"}
    assert rec["grade"] == 1
    assert rec["grade_label"] == "已确认播放"
    assert rec["channel"] == "primary"
    assert rec["message"] == "播报成功(已确认播放)"
    assert rec["warnings"] == ["w1"]
    assert rec["device"]["hardware"] == "LX06"
    assert rec["synth"]["ok"] is True
    assert rec["observation"]["saw_playing"] is True
    assert rec["timestamp"]


def test_ring_eviction_keeps_latest_ten():
    for i in range(12):
        diagnostics.record_broadcast({"grade": 1, "channel": "primary",
                                      "message": "m%d" % i})
    records = diagnostics.get_broadcasts()
    assert len(records) == 10
    assert records[0]["message"] == "m11"  # 最新在前
    assert records[-1]["message"] == "m2"


def test_unavailable_elements_annotated():
    diagnostics.record_broadcast({
        "grade": 2, "grade_label": "指令已接受未确认播放",
        "channel": "mibrain", "message": "指令已接受未确认播放",
        "warnings": [],
        "device": {"device_id": "d1", "name": "小爱音箱",
                   "hardware": "LX06", "is_speaker": True},
        "synth": {},
        "observation": {"saw_playing": False, "last_state": None,
                        "poll_count": 0, "volume": None,
                        "note": "观察不可用"},
    })
    rec = diagnostics.get_broadcasts()[0]
    assert rec["synth"] == {}  # mibrain 通道无合成要素
    assert rec["observation"]["note"] == "观察不可用"
    assert rec["observation"]["last_state"] is None


def test_api_dict_key_whitelist_no_credentials():
    diagnostics.record_broadcast({
        "grade": 1, "channel": "primary", "message": "m",
        "grade_label": "已确认播放", "timestamp": "t",
        "device": {}, "synth": {}, "observation": {}, "warnings": [],
        "pass_token": "SECRET_PT", "service_token": "SECRET_ST",
    })
    rec = diagnostics.get_broadcasts()[0]
    blob = json.dumps(rec, ensure_ascii=False)
    for secret in ("pass_token", "service_token", "SECRET_PT", "SECRET_ST"):
        assert secret not in blob


def test_self_check_unknown_to_ok(monkeypatch):
    calls = _install_self_check_mocks(monkeypatch, lambda url: _OkResponse())
    assert diagnostics.self_check_status()["status"] == "unknown"
    diagnostics.run_audio_self_check()
    status = diagnostics.self_check_status()
    assert status["status"] == "ok"
    assert status["checked_at"]
    assert status["message"] is None
    assert len(calls) == 1


def test_self_check_no_rerun_after_success(monkeypatch):
    calls = _install_self_check_mocks(monkeypatch, lambda url: _OkResponse())
    diagnostics.run_audio_self_check()
    assert len(calls) == 1
    diagnostics.run_audio_self_check()
    assert len(calls) == 1  # 进程内不重复自检


def test_self_check_failure_retries_then_failed(monkeypatch, caplog):
    def _fail(url):
        raise RuntimeError("conn refused")

    calls = _install_self_check_mocks(monkeypatch, _fail)
    with caplog.at_level(logging.WARNING, logger="lit.xiaoai.diagnostics"):
        diagnostics.run_audio_self_check()
    assert len(calls) == 3  # 首次 + 2 次重试
    status = diagnostics.self_check_status()
    assert status["status"] == "failed"
    assert "conn refused" in status["message"]
    assert "自检失败" in caplog.text