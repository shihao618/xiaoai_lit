"""XiaoAISpeaker 测试：凭证状态机、设备管理、云端原生播报（MIoT 主通道 + mibrain 回退）。"""
import json
import logging
from collections import deque
from datetime import datetime

import pytest

from lit import config
from lit.xiaoai import diagnostics, run_async
from lit.xiaoai.account import PollResult
from lit.xiaoai.credentials import CredentialStore, Credentials
from lit.xiaoai.exceptions import (
    CommandRejectedError,
    CredentialRejectedError,
    NetworkError,
    RateLimitedError,
    RequestTimeoutError,
    XiaoAIError,
)
from lit.xiaoai.mina import PlayerStatus, SpeakerDevice
from lit.xiaoai.speaker import XiaoAISpeaker


class StubMina:
    def __init__(self, devices=None, tts_exc=None, list_exc=None,
                 status_exc=None, status_state="playing"):
        self.devices = devices or []
        self.tts_calls = []
        self.status_state = status_state
        self.tts_exc = tts_exc
        self.list_exc = list_exc
        self.status_exc = status_exc
        self.list_calls = 0

    def device_list(self, cred):
        self.list_calls += 1
        if self.list_exc:
            raise self.list_exc
        return list(self.devices)

    def text_to_speech(self, cred, device_id, text):
        self.tts_calls.append((device_id, text))
        if self.tts_exc:
            raise self.tts_exc

    def get_play_status(self, cred, device_id):
        if self.status_exc:
            raise self.status_exc
        return PlayerStatus(volume=50, state=self.status_state)


class StubMiot:
    """MIoT 原生播报客户端替身。"""

    def __init__(self, playing=True, exc=None, stop_exc=None):
        self.playing = playing
        self.exc = exc
        self.stop_exc = stop_exc
        self.texts = []
        self.stop_calls = []

    def play_text(self, cred, miot_did, text):
        if self.exc:
            raise self.exc
        self.texts.append((miot_did, text))
        return {"code": 0}

    def playing_state(self, cred, miot_did):
        return 1 if self.playing else 0

    def stop(self, cred, miot_did):
        if self.stop_exc:
            raise self.stop_exc
        self.stop_calls.append(miot_did)
        return {"code": 0}


class StubAccount:
    def start_qr(self):
        return ("https://account.xiaomi.com/qr?ticket=t",
                "https://account.xiaomi.com/lp?x=1", "abc123def4567890")

    def poll_qr(self, poll_url):
        return PollResult("waiting", next_poll_url=poll_url)

    def exchange(self, user_id, pass_token, device_id):
        return {"user_id": user_id, "pass_token": pass_token,
                "device_id": device_id, "service_token": "st", "security": "sec"}


DEV = SpeakerDevice("d1", "小爱音箱", "LX06", "1.1.1.1", "800740001")
DEV_MIOT = SpeakerDevice("d1", "小爱音箱", "L05B", "1.1.1.1", "800740573")
LAMP = SpeakerDevice("d2", "台灯", "MJCTD01", "2.2.2.2")


def make_speaker(tmp_path, mina=None, account=None, miot=None):
    store = CredentialStore(data_dir=str(tmp_path))
    return XiaoAISpeaker(store=store, mina=mina or StubMina(),
                         account=account or StubAccount(),
                         miot=miot or StubMiot())


def login(speaker, devices=None):
    speaker._commit_credentials(
        Credentials(user_id="42", pass_token="pt", device_id="dev",
                    service_token="st", refreshed_at=datetime.now()),
        devices if devices is not None else [DEV_MIOT])


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr("lit.xiaoai.speaker.time.sleep", lambda s: None)


@pytest.fixture(autouse=True)
def _isolate_diagnostics(monkeypatch):
    monkeypatch.setattr(diagnostics, "_records", deque(maxlen=10))


# ---------- run_async 垫片 ----------

def test_run_async_passthrough():
    assert run_async("x") == "x"
    assert run_async(None) is None
    assert run_async([1, 2]) == [1, 2]


def test_run_async_coroutine():
    async def coro():
        return 42
    assert run_async(coro()) == 42


# ---------- 状态机 ----------

def test_init_no_credential(tmp_path):
    sp = make_speaker(tmp_path)
    assert sp.logged_in is False
    assert sp.cookie_valid is False
    assert sp.get_status()["state"] == "no_credential"


def _save_usable_credentials(tmp_path):
    store = CredentialStore(data_dir=str(tmp_path))
    store.save(Credentials(user_id="42", pass_token="pt",
                           service_token="st", refreshed_at=datetime.now()))
    return store


def test_70016_marks_rejected(tmp_path):
    mina = StubMina(list_exc=CredentialRejectedError("70016"))
    sp = XiaoAISpeaker(store=_save_usable_credentials(tmp_path), mina=mina,
                       account=StubAccount(), miot=StubMiot())
    assert sp.check_cookie_valid() is False
    assert sp.get_status()["state"] == "rejected"


def test_network_error_keeps_state(tmp_path):
    mina = StubMina(list_exc=NetworkError("boom"))
    sp = XiaoAISpeaker(store=_save_usable_credentials(tmp_path), mina=mina,
                       account=StubAccount(), miot=StubMiot())
    assert sp.get_status()["state"] == "pending_verify"
    assert sp.check_cookie_valid() is False
    assert sp.get_status()["state"] == "pending_verify"


def test_rate_limited_keeps_state(tmp_path):
    mina = StubMina(list_exc=RateLimitedError("70022"))
    sp = XiaoAISpeaker(store=_save_usable_credentials(tmp_path), mina=mina,
                       account=StubAccount(), miot=StubMiot())
    assert sp.check_cookie_valid() is False
    assert sp.get_status()["state"] == "pending_verify"


def test_restore_temporary_error_pending_verify(tmp_path):
    store = _save_usable_credentials(tmp_path)
    mina = StubMina(list_exc=NetworkError("temp"))
    sp = XiaoAISpeaker(store=store, mina=mina, account=StubAccount(),
                       miot=StubMiot())
    sp._restore_worker()
    assert sp.get_status()["state"] == "pending_verify"


def test_restore_success(tmp_path):
    store = _save_usable_credentials(tmp_path)
    mina = StubMina(devices=[DEV_MIOT])
    sp = XiaoAISpeaker(store=store, mina=mina, account=StubAccount(),
                       miot=StubMiot())
    sp._restore_worker()
    assert sp.logged_in is True
    assert len(sp.get_devices()) == 1


def test_restore_rejected(tmp_path):
    store = _save_usable_credentials(tmp_path)
    mina = StubMina(list_exc=CredentialRejectedError("70016"))
    sp = XiaoAISpeaker(store=store, mina=mina, account=StubAccount(),
                       miot=StubMiot())
    sp._restore_worker()
    assert sp.get_status()["state"] == "rejected"


# ---------- 云端播报：MIoT 主通道 ----------

def test_speak_success_two_segments(tmp_path, no_sleep):
    miot = StubMiot(playing=True)
    sp = make_speaker(tmp_path, miot=miot)
    login(sp)
    ok, msg, detail = sp.speak_with_detail("第一节高数")
    assert ok is True
    assert msg == "播报成功(已确认播放)"
    assert detail["channel"] == "miot"
    assert detail["grade"] == 1
    obs = detail["observation"]
    assert obs["saw_playing"] is True
    assert obs["last_state"] == "playing"
    assert obs["poll_count"] >= 1
    assert [t for _did, t in miot.texts] == ["叮叮叮叮！上课提醒！", "第一节高数"]


def test_speak_unobserved_grade2(tmp_path, no_sleep):
    sp = make_speaker(tmp_path, miot=StubMiot(playing=False))
    login(sp)
    ok, msg, detail = sp.speak_with_detail("第一节高数")
    assert ok is True
    assert msg == "指令已接受未确认播放"
    assert detail["grade"] == 2


def test_speak_not_logged_in_fast_fail(tmp_path, no_sleep):
    sp = make_speaker(tmp_path)
    ok, msg, detail = sp.speak_with_detail("x")
    assert ok is False
    assert "未登录" in msg
    assert detail["channel"] == "none"
    assert detail["grade"] == 3


def test_speak_timeout_no_retry_no_fallback(tmp_path, no_sleep):
    miot = StubMiot(exc=RequestTimeoutError("timeout"))
    sp = make_speaker(tmp_path, miot=miot)
    login(sp)
    ok, msg, detail = sp.speak_with_detail("x")
    assert ok is False
    assert "结果未知" in msg
    assert detail["channel"] == "miot"
    assert detail["grade"] == 3


def test_speak_rate_limited_gives_up(tmp_path, no_sleep):
    miot = StubMiot(exc=RateLimitedError("70022"))
    sp = make_speaker(tmp_path, miot=miot)
    login(sp)
    ok, msg, detail = sp.speak_with_detail("x")
    assert ok is False
    assert "频繁" in msg
    assert detail["grade"] == 3


def test_speak_credential_rejected_terminates(tmp_path, no_sleep):
    miot = StubMiot(exc=CredentialRejectedError("70016"))
    sp = make_speaker(tmp_path, miot=miot)
    login(sp)
    ok, msg, detail = sp.speak_with_detail("x")
    assert ok is False
    assert "失效" in msg
    assert sp.get_status()["state"] == "rejected"


def test_alarm_and_speak_returns_bool(tmp_path, no_sleep):
    sp = make_speaker(tmp_path)
    login(sp)
    assert sp.alarm_and_speak("测试") is True


def test_speak_with_result_thin_wrapper(tmp_path, no_sleep):
    sp = make_speaker(tmp_path)
    login(sp)
    ok2, msg2 = sp.speak_with_result("x")
    ok3, msg3, _detail = sp.speak_with_detail("x")
    assert (ok2, msg2) == (ok3, msg3)


# ---------- 云端播报：mibrain 回退 ----------

def test_miot_failure_falls_back_mibrain(tmp_path, no_sleep):
    mina = StubMina(devices=[DEV_MIOT], status_state="playing")
    miot = StubMiot(exc=NetworkError("miot down"))
    sp = make_speaker(tmp_path, mina=mina, miot=miot)
    login(sp)
    ok, _msg, detail = sp.speak_with_detail("第一节高数")
    assert ok is True
    assert detail["channel"] == "mibrain"
    assert len(mina.tts_calls) == 2
    assert any("MIoT" in w for w in detail["warnings"])


def test_mibrain_model_silent_warning_for_l05b(tmp_path, no_sleep):
    mina = StubMina(devices=[DEV_MIOT], status_state="paused")
    miot = StubMiot(exc=NetworkError("miot down"))
    sp = make_speaker(tmp_path, mina=mina, miot=miot)
    login(sp)
    ok, _msg, detail = sp.speak_with_detail("x")
    assert ok is True
    assert detail["channel"] == "mibrain"
    assert any("静默忽略" in w for w in detail["warnings"])


def test_mibrain_normal_model_no_model_warning(tmp_path, no_sleep):
    mina = StubMina(devices=[DEV], status_state="paused")
    miot = StubMiot(exc=NetworkError("miot down"))
    sp = make_speaker(tmp_path, mina=mina, miot=miot)
    login(sp, devices=[DEV])
    ok, _msg, detail = sp.speak_with_detail("x")
    assert ok is True
    assert not any("静默忽略" in w for w in detail["warnings"])


def test_mibrain_weak_observation_grade2(tmp_path, no_sleep):
    mina = StubMina(devices=[DEV_MIOT], status_state="paused")
    miot = StubMiot(exc=NetworkError("miot down"))
    sp = make_speaker(tmp_path, mina=mina, miot=miot)
    login(sp)
    ok, _msg, detail = sp.speak_with_detail("x")
    assert ok is True
    assert detail["grade"] == 2
    assert detail["observation"]["saw_playing"] is False


def test_observation_unavailable_keeps_grade2(tmp_path, no_sleep):
    mina = StubMina(devices=[DEV_MIOT], status_exc=NetworkError("status down"))
    miot = StubMiot(exc=NetworkError("miot down"))
    sp = make_speaker(tmp_path, mina=mina, miot=miot)
    login(sp)
    ok, _msg, detail = sp.speak_with_detail("x")
    assert ok is True
    assert detail["grade"] == 2


def test_both_channels_fail_grade3(tmp_path, no_sleep):
    mina = StubMina(devices=[DEV_MIOT], tts_exc=NetworkError("tts down"))
    miot = StubMiot(exc=NetworkError("miot down"))
    sp = make_speaker(tmp_path, mina=mina, miot=miot)
    login(sp)
    ok, msg, detail = sp.speak_with_detail("x")
    assert ok is False
    assert detail["grade"] == 3
    assert "失败" in msg


def test_non_speaker_target_risk_flagged(tmp_path, no_sleep):
    mina = StubMina(devices=[LAMP])
    sp = make_speaker(tmp_path, mina=mina)
    login(sp, devices=[LAMP])
    ok, _msg, detail = sp.speak_with_detail("x")
    assert ok is True
    assert detail["device"]["is_speaker"] is False
    assert any("非音箱" in w for w in detail["warnings"])


def test_broadcast_record_elements(tmp_path, no_sleep):
    sp = make_speaker(tmp_path)
    login(sp)
    sp.speak_with_detail("记录测试")
    records = diagnostics.get_broadcasts()
    assert len(records) == 1
    record = records[0]
    assert set(record) == {"timestamp", "grade", "grade_label", "channel",
                           "message", "warnings", "device", "observation"}
    assert record["channel"] == "miot"
    assert record["device"]["device_id"] == "d1"


def test_log_grade1_wording(tmp_path, no_sleep, caplog):
    sp = make_speaker(tmp_path)
    login(sp)
    with caplog.at_level(logging.INFO, logger="lit.xiaoai.speaker"):
        sp.speak_with_detail("日志测试")
    assert "小爱播报成功(已确认播放)" in caplog.text
    assert "通道=miot" in caplog.text


# ---------- 设备管理 ----------

def test_get_status_compat_fields(tmp_path):
    sp = make_speaker(tmp_path)
    login(sp)
    status = sp.get_status()
    assert status["logged_in"] is True
    for key in ("cookie_valid", "login_time", "username", "device_id",
                "devices", "state"):
        assert key in status


def test_miot_did_of_device(tmp_path):
    sp = make_speaker(tmp_path)
    login(sp)
    assert sp._miot_did_of("d1") == "800740573"
    assert sp._miot_did_of("missing") == ""


def test_get_devices_requires_login(tmp_path):
    sp = make_speaker(tmp_path)
    with pytest.raises(XiaoAIError):
        sp.get_devices()


def test_get_devices_returns_api_dicts(tmp_path):
    mina = StubMina(devices=[DEV_MIOT])
    sp = make_speaker(tmp_path, mina=mina)
    login(sp)
    devices = sp.get_devices()
    assert set(devices[0]) == {"deviceID", "name", "hardware", "address",
                               "is_speaker"}


def test_select_device_speaker_no_warning(tmp_path):
    sp = make_speaker(tmp_path)
    login(sp)
    result = sp.select_device("d1")
    assert result["is_speaker"] is True
    assert result["warning"] is None


def test_select_device_non_speaker_warning(tmp_path):
    sp = make_speaker(tmp_path)
    login(sp, devices=[LAMP])
    result = sp.select_device("d2")
    assert result["is_speaker"] is False
    assert "非物理音箱" in result["warning"]


# ---------- 停止播放（MIoT） ----------

def test_stop_playback_success(tmp_path):
    miot = StubMiot()
    sp = make_speaker(tmp_path, miot=miot)
    login(sp)
    ok, message = sp.stop_playback()
    assert ok is True
    assert "已发送" in message
    assert miot.stop_calls == ["800740573"]


def test_stop_playback_not_logged_in(tmp_path):
    sp = make_speaker(tmp_path)
    ok, message = sp.stop_playback()
    assert ok is False
    assert "未登录" in message


def test_stop_playback_miot_failure(tmp_path):
    miot = StubMiot(stop_exc=NetworkError("stop down"))
    sp = make_speaker(tmp_path, miot=miot)
    login(sp)
    ok, message = sp.stop_playback()
    assert ok is False
    assert "失败" in message


# ---------- 课程提醒 ----------

def test_remind_morning_text(tmp_path, no_sleep):
    miot = StubMiot(playing=True)
    sp = make_speaker(tmp_path, miot=miot)
    login(sp)
    courses = [{"name": "高等数学", "classroom": "A101",
                "start_time": "08:00:00"}]
    assert sp.remind_morning(courses) is True
    body = miot.texts[-1][1]
    assert "上午" in body
    assert "高等数学" in body
    assert "A101" in body


def test_remind_afternoon_no_courses(tmp_path, no_sleep):
    miot = StubMiot(playing=True)
    sp = make_speaker(tmp_path, miot=miot)
    login(sp)
    assert sp.remind_afternoon([]) is True
    assert "没有课程" in miot.texts[-1][1]


def test_remind_isolated_from_exceptions(tmp_path, no_sleep):
    mina = StubMina(tts_exc=NetworkError("tts down"))
    miot = StubMiot(exc=RuntimeError("boom"))
    sp = make_speaker(tmp_path, mina=mina, miot=miot)
    login(sp)
    assert sp.remind_morning([{"name": "x", "start_time": "08:00:00"}]) is False
