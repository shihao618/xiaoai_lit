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
    def __init__(self, devices=None, tts_exc=None, url_exc=None, list_exc=None,
                 status_exc=None):
        self.devices = devices or []
        self.tts_calls = []
        self.url_calls = []
        self.stop_calls = []
        self.status_seq = []
        self.status_state = "playing"
        self.tts_exc = tts_exc
        self.url_exc = url_exc
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

    def play_url(self, cred, device_id, url, hardware=None, tail_url=None):
        self.url_calls.append((device_id, url))
        if self.url_exc:
            raise self.url_exc

    def get_play_status(self, cred, device_id):
        if self.status_exc:
            raise self.status_exc
        if self.status_seq:
            state = self.status_seq.pop(0)
        else:
            state = self.status_state
        return PlayerStatus(volume=50, state=state)

    def stop_playback(self, cred, device_id):
        self.stop_calls.append(device_id)


class StubAccount:
    def start_qr(self):
        return ("https://account.xiaomi.com/qr?ticket=t",
                "https://account.xiaomi.com/lp?x=1", "abc123def4567890")

    def poll_qr(self, poll_url):
        return PollResult("waiting", next_poll_url=poll_url)

    def exchange(self, user_id, pass_token, device_id):
        return {"user_id": user_id, "pass_token": pass_token,
                "device_id": device_id, "service_token": "st", "security": "sec"}


DEV = SpeakerDevice("d1", "小爱音箱", "LX06", "1.1.1.1")


def make_speaker(tmp_path, mina=None, account=None):
    store = CredentialStore(data_dir=str(tmp_path))
    return XiaoAISpeaker(store=store, mina=mina or StubMina(),
                         account=account or StubAccount())


def login(speaker, devices=None):
    speaker._commit_credentials(
        Credentials(user_id="42", pass_token="pt", device_id="dev",
                    service_token="st", refreshed_at=datetime.now()),
        devices if devices is not None else [DEV])


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr("lit.xiaoai.speaker.time.sleep", lambda s: None)


@pytest.fixture(autouse=True)
def _isolate_diagnostics(monkeypatch):
    monkeypatch.setattr(diagnostics, "_records", deque(maxlen=10))
    monkeypatch.setattr(diagnostics, "_self_check",
                        {"status": "unknown", "checked_at": None, "message": None})
    monkeypatch.setattr(diagnostics, "_self_check_done", False)


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


def test_70016_marks_rejected(tmp_path):
    mina = StubMina(list_exc=CredentialRejectedError("70016"))
    sp = make_speaker(tmp_path, mina=mina)
    login(sp)
    assert sp.check_cookie_valid() is False
    status = sp.get_status()
    assert status["state"] == "rejected"
    assert status["logged_in"] is False
    assert status["cookie_valid"] is False
    data = json.loads(
        (tmp_path / "xiaoai_credentials.json").read_text(encoding="utf-8"))
    assert data["rejected"] is True


def test_network_error_keeps_state(tmp_path):
    mina = StubMina(list_exc=NetworkError("net down"))
    sp = make_speaker(tmp_path, mina=mina)
    login(sp)
    assert sp.check_cookie_valid() is False
    assert sp.get_status()["state"] == "logged_in"
    data = json.loads(
        (tmp_path / "xiaoai_credentials.json").read_text(encoding="utf-8"))
    assert data["rejected"] is False


def test_rate_limited_keeps_state(tmp_path):
    mina = StubMina(list_exc=RateLimitedError("429"))
    sp = make_speaker(tmp_path, mina=mina)
    login(sp)
    assert sp.check_cookie_valid() is False
    assert sp.get_status()["state"] == "logged_in"


def test_restore_temporary_error_pending_verify(tmp_path):
    mina = StubMina(list_exc=NetworkError("net down"))
    CredentialStore(data_dir=str(tmp_path)).save(
        Credentials(user_id="1", service_token="s", refreshed_at=datetime.now()))
    sp = make_speaker(tmp_path, mina=mina)
    status = sp.get_status()
    assert status["state"] == "pending_verify"
    assert status["cookie_valid"] is True
    assert status["logged_in"] is False
    sp._restore_worker()
    assert sp.get_status()["state"] == "pending_verify"
    data = json.loads(
        (tmp_path / "xiaoai_credentials.json").read_text(encoding="utf-8"))
    assert data["user_id"] == "1"  # 凭证保留
    mina.list_exc = None
    mina.devices = [DEV]
    assert sp.check_cookie_valid() is True
    assert sp.logged_in


def test_restore_success(tmp_path):
    mina = StubMina(devices=[DEV])
    CredentialStore(data_dir=str(tmp_path)).save(
        Credentials(user_id="1", service_token="s", refreshed_at=datetime.now()))
    sp = make_speaker(tmp_path, mina=mina)
    sp._restore_worker()
    assert sp.logged_in
    assert sp.get_status()["devices"][0]["deviceID"] == "d1"


def test_restore_rejected(tmp_path):
    mina = StubMina(list_exc=CredentialRejectedError("70016"))
    CredentialStore(data_dir=str(tmp_path)).save(
        Credentials(user_id="1", service_token="s", refreshed_at=datetime.now()))
    sp = make_speaker(tmp_path, mina=mina)
    sp._restore_worker()
    assert sp.get_status()["state"] == "rejected"


# ---------- 播报四分支 ----------

def test_speak_success_two_segments(tmp_path, no_sleep):
    mina = StubMina()
    sp = make_speaker(tmp_path, mina=mina)
    login(sp)
    ok, msg = sp.speak_with_result("第一节高数")
    assert ok is True
    assert len(mina.tts_calls) == 2
    assert mina.tts_calls[0][1] == "叮叮叮叮！上课提醒！"
    assert mina.tts_calls[1][1] == "第一节高数"


def test_speak_not_logged_in_fast_fail(tmp_path, no_sleep):
    mina = StubMina()
    sp = make_speaker(tmp_path, mina=mina)
    ok, msg = sp.speak_with_result("x")
    assert ok is False
    assert "未登录" in msg
    assert mina.tts_calls == []
    assert mina.list_calls == 0


def test_speak_timeout_no_retry_no_fallback(tmp_path, no_sleep):
    mina = StubMina(tts_exc=RequestTimeoutError("t"))
    sp = make_speaker(tmp_path, mina=mina)
    login(sp)
    ok, msg = sp.speak_with_result("x", backup_url="https://example.com/a.mp3")
    assert ok is False
    assert "未知" in msg
    assert len(mina.tts_calls) == 1
    assert mina.url_calls == []


def test_speak_rejected_falls_back_to_url(tmp_path, no_sleep, monkeypatch):
    _fast_clock(monkeypatch)
    mina = StubMina(tts_exc=CommandRejectedError("code=-500"))
    mina.status_state = "paused"
    sp = make_speaker(tmp_path, mina=mina)
    login(sp)
    ok, msg = sp.speak_with_result("详情", backup_url="https://example.com/a.mp3")
    assert ok is True
    assert len(mina.url_calls) == 2
    assert mina.url_calls[0][1] == "https://example.com/a.mp3"
    assert mina.url_calls[1][1] == "https://example.com/a.mp3"
    assert mina.stop_calls == ["d1", "d1"]  # 两段回退播放后均 pause 防循环


def test_speak_rejected_no_backup_fails(tmp_path, no_sleep):
    mina = StubMina(tts_exc=CommandRejectedError("rejected"))
    sp = make_speaker(tmp_path, mina=mina)
    login(sp)
    ok, msg = sp.speak_with_result("x")
    assert ok is False
    assert "未接受" in msg


def test_speak_rate_limited_gives_up(tmp_path, no_sleep):
    mina = StubMina(tts_exc=RateLimitedError("429"))
    sp = make_speaker(tmp_path, mina=mina)
    login(sp)
    ok, msg = sp.speak_with_result("x", backup_url="https://example.com/a.mp3")
    assert ok is False
    assert mina.url_calls == []


def test_speak_credential_rejected_terminates(tmp_path, no_sleep):
    mina = StubMina(tts_exc=CredentialRejectedError("70016"))
    sp = make_speaker(tmp_path, mina=mina)
    login(sp)
    ok, msg = sp.speak_with_result("x", backup_url="https://example.com/a.mp3")
    assert ok is False
    assert sp.get_status()["state"] == "rejected"
    assert mina.url_calls == []


def test_alarm_and_speak_returns_bool(tmp_path, no_sleep):
    mina = StubMina()
    sp = make_speaker(tmp_path, mina=mina)
    login(sp)
    assert sp.alarm_and_speak("测试") is True


def _enable_local_tts(monkeypatch):
    monkeypatch.setattr(config, "XIAOAI_TTS_BASE_URL",
                        "http://127.0.0.1:5000")
    monkeypatch.setattr(
        "lit.xiaoai.tts.synthesize_announcement",
        lambda prompt, text: ("j.mp3", 10.0, 0.5))
    monkeypatch.setattr(
        "lit.xiaoai.tts.audio_url",
        lambda f: "http://127.0.0.1:5000/audio/tts/" + f)
    monkeypatch.setattr(
        "lit.xiaoai.tts.silence_url",
        lambda: "http://127.0.0.1:5000/audio/tts/silence30.mp3")


def _fast_clock(monkeypatch):
    t = [0.0]

    def fake_monotonic():
        t[0] += 2.0
        return t[0]

    monkeypatch.setattr("lit.xiaoai.speaker.time.monotonic", fake_monotonic)


def test_speak_local_tts_stops_when_status_stuck_paused(
        tmp_path, no_sleep, monkeypatch):
    _enable_local_tts(monkeypatch)
    _fast_clock(monkeypatch)
    mina = StubMina()
    mina.status_state = "paused"
    sp = make_speaker(tmp_path, mina=mina)
    login(sp)
    ok, _ = sp.speak_with_result("第一节高数")
    assert ok is True
    assert len(mina.url_calls) == 1  # 单文件（提示语+间隔+正文）一次播放
    assert mina.tts_calls == []
    assert mina.stop_calls == ["d1"]


def test_speak_local_tts_early_stop_on_stopped(tmp_path, no_sleep, monkeypatch):
    _enable_local_tts(monkeypatch)
    _fast_clock(monkeypatch)
    mina = StubMina()
    mina.status_seq = ["playing", "playing", "stopped", "paused"]
    sp = make_speaker(tmp_path, mina=mina)
    login(sp)
    ok, _ = sp.speak_with_result("第一节高数")
    assert ok is True
    assert len(mina.url_calls) == 1
    assert mina.stop_calls == ["d1"]


def test_play_url_direct(tmp_path, no_sleep, monkeypatch):
    _fast_clock(monkeypatch)
    mina = StubMina()
    mina.status_state = "paused"
    sp = make_speaker(tmp_path, mina=mina)
    login(sp)
    ok, msg, detail = sp.play_url_direct("https://example.com/a.mp3")
    assert ok is True
    assert mina.url_calls == [("d1", "https://example.com/a.mp3")]
    assert mina.stop_calls == ["d1"]  # 备份播放后 pause 防循环


def test_play_url_direct_not_logged_in(tmp_path):
    sp = make_speaker(tmp_path)
    ok, msg, detail = sp.play_url_direct("https://example.com/a.mp3")
    assert ok is False
    assert "未登录" in msg


# ---------- remind_* 异常隔离 ----------

def test_remind_isolated_from_exceptions(tmp_path, no_sleep):
    mina = StubMina(tts_exc=RuntimeError("unexpected"))
    sp = make_speaker(tmp_path, mina=mina)
    login(sp)
    result = sp.remind_morning(
        [{"name": "高数", "classroom": "A101", "section_start": 1}])
    assert result is False  # 不抛异常


def test_remind_morning_text(tmp_path, no_sleep):
    mina = StubMina()
    sp = make_speaker(tmp_path, mina=mina)
    login(sp)
    assert sp.remind_morning(
        [{"name": "高数", "classroom": "A101", "section_start": 1}]) is True
    detail = mina.tts_calls[1][1]
    assert "上午" in detail
    assert "高数" in detail
    assert "A101" in detail


def test_remind_afternoon_no_courses(tmp_path, no_sleep):
    mina = StubMina()
    sp = make_speaker(tmp_path, mina=mina)
    login(sp)
    assert sp.remind_afternoon([]) is True
    assert "没有课程安排" in mina.tts_calls[1][1]



# ---------- 兼容契约 ----------

def test_get_status_compat_fields(tmp_path):
    sp = make_speaker(tmp_path)
    status = sp.get_status()
    for key in ("logged_in", "cookie_valid", "login_time", "username",
                "device_id", "devices"):
        assert key in status
    assert "state" in status


def test_device_id_cache_attribute_exists(tmp_path):
    sp = make_speaker(tmp_path)
    assert hasattr(sp, "_device_id_cache")
    login(sp)
    sp.select_device("speaker-x")
    assert sp._device_id_cache == "speaker-x"
    assert sp.device_id == "speaker-x"
    data = json.loads(
        (tmp_path / "xiaoai_credentials.json").read_text(encoding="utf-8"))
    assert data["default_device_id"] == "speaker-x"


def test_relogin_keeps_default_device(tmp_path):
    sp = make_speaker(tmp_path)
    login(sp)
    sp.select_device("speaker-x")
    sp._commit_credentials(
        Credentials(user_id="99", service_token="s2",
                    refreshed_at=datetime.now()), [])
    assert sp._device_id_cache == "speaker-x"
    assert sp._cred.default_device_id == "speaker-x"


def test_get_devices_requires_login(tmp_path):
    sp = make_speaker(tmp_path)
    with pytest.raises(XiaoAIError):
        sp.get_devices()


def test_get_devices_returns_api_dicts(tmp_path):
    mina = StubMina(devices=[DEV, SpeakerDevice("d2", "台灯", "MJ", "2.2.2.2")])
    sp = make_speaker(tmp_path, mina=mina)
    login(sp)
    devices = sp.get_devices()
    assert devices[0] == {"deviceID": "d1", "name": "小爱音箱",
                          "hardware": "LX06", "address": "1.1.1.1",
                          "is_speaker": True}
    assert devices[1]["name"] == "台灯"
    assert devices[1]["is_speaker"] is False


def test_get_devices_70016_marks_rejected(tmp_path):
    mina = StubMina(list_exc=CredentialRejectedError("70016"))
    sp = make_speaker(tmp_path, mina=mina)
    login(sp)
    with pytest.raises(CredentialRejectedError):
        sp.get_devices()
    assert sp.get_status()["state"] == "rejected"

# ---------- N2 分级与诊断 ----------

L05B = SpeakerDevice("l05", "小爱音箱Pro", "L05B", "9.9.9.9")
LAMP = SpeakerDevice("d2", "台灯", "MJCTD01", "2.2.2.2")
ROBOT = SpeakerDevice("d3", "扫地机器人", None, "3.3.3.3")


def test_primary_channel_playing_grade1(tmp_path, no_sleep, monkeypatch):
    _enable_local_tts(monkeypatch)
    _fast_clock(monkeypatch)
    mina = StubMina()
    sp = make_speaker(tmp_path, mina=mina)
    login(sp)
    ok, msg, detail = sp.speak_with_detail("第一节高数")
    assert ok is True
    assert detail["grade"] == 1
    assert detail["channel"] == "primary"
    assert detail["observation"]["saw_playing"] is True
    assert "已确认播放" in msg


def test_l05b_stuck_paused_grade2(tmp_path, no_sleep, monkeypatch):
    _enable_local_tts(monkeypatch)
    _fast_clock(monkeypatch)
    mina = StubMina()
    mina.status_state = "paused"
    sp = make_speaker(tmp_path, mina=mina)
    login(sp)
    ok, msg, detail = sp.speak_with_detail("第一节高数")
    assert ok is True
    assert detail["grade"] == 2
    assert detail["grade_label"] == "指令已接受未确认播放"
    assert detail["observation"]["saw_playing"] is False
    assert detail["observation"]["last_state"] == "paused"
    assert "成功" not in msg


def test_fallback_mibrain_play_music_model_warning(tmp_path, no_sleep, monkeypatch):
    _enable_local_tts(monkeypatch)
    monkeypatch.setattr(
        "lit.xiaoai.tts.synthesize_announcement",
        lambda prompt, text: ("", 0.0, 0.1))
    mina = StubMina(devices=[L05B])
    mina.status_state = "paused"
    sp = make_speaker(tmp_path, mina=mina)
    login(sp, devices=[L05B])
    ok, msg, detail = sp.speak_with_detail("x")
    assert ok is True
    assert detail["grade"] == 2
    assert detail["channel"] == "mibrain"
    assert detail["synth"]["ok"] is False
    assert any("静默忽略" in w for w in detail["warnings"])


def test_fallback_mibrain_normal_model_no_model_warning(tmp_path, no_sleep):
    mina = StubMina(devices=[LAMP])
    mina.status_state = "paused"
    sp = make_speaker(tmp_path, mina=mina)
    login(sp, devices=[LAMP])
    ok, msg, detail = sp.speak_with_detail("x")
    assert ok is True
    assert detail["grade"] == 2
    assert detail["channel"] == "mibrain"
    assert not any("静默忽略" in w for w in detail["warnings"])


def test_observation_unavailable_grade2(tmp_path, no_sleep):
    mina = StubMina(status_exc=RuntimeError("status down"))
    sp = make_speaker(tmp_path, mina=mina)
    login(sp)
    ok, msg, detail = sp.speak_with_detail("x")
    assert ok is True
    assert detail["grade"] == 2
    assert detail["observation"]["note"] == "观察不可用"
    assert detail["observation"]["poll_count"] == 0


def test_both_channels_fail_grade3(tmp_path, no_sleep, monkeypatch):
    monkeypatch.setattr(config, "XIAOAI_TTS_BASE_URL", "http://192.168.0.9:5000")
    monkeypatch.setattr(
        "lit.xiaoai.tts.synthesize_announcement",
        lambda prompt, text: ("", 0.0, 0.1))
    mina = StubMina(tts_exc=CommandRejectedError("code=-500"))
    sp = make_speaker(tmp_path, mina=mina)
    login(sp)
    ok, msg, detail = sp.speak_with_detail("x")
    assert ok is False
    assert detail["grade"] == 3
    assert detail["channel"] == "mibrain"
    assert "本地TTS合成失败" in "".join(detail["warnings"])
    assert "未接受指令" in msg


def test_empty_base_url_mibrain_with_warning(
        tmp_path, no_sleep, monkeypatch, caplog):
    monkeypatch.setattr(config, "XIAOAI_TTS_BASE_URL", "")
    mina = StubMina()
    sp = make_speaker(tmp_path, mina=mina)
    login(sp)
    with caplog.at_level(logging.WARNING, logger="lit.xiaoai.speaker"):
        ok, msg, detail = sp.speak_with_detail("x")
    assert ok is True
    assert detail["channel"] == "mibrain"
    assert "主通道未启用" in caplog.text
    assert any("未配置" in w for w in detail["warnings"])


def test_localhost_base_url_loopback_warning(
        tmp_path, no_sleep, monkeypatch, caplog):
    _enable_local_tts(monkeypatch)
    monkeypatch.setattr(config, "XIAOAI_TTS_BASE_URL", "http://localhost:10379")
    _fast_clock(monkeypatch)
    mina = StubMina()
    sp = make_speaker(tmp_path, mina=mina)
    login(sp)
    with caplog.at_level(logging.WARNING, logger="lit.xiaoai.speaker"):
        ok, msg, detail = sp.speak_with_detail("x")
    assert ok is True
    assert "localhost" in caplog.text
    assert "回环" in caplog.text
    assert any("localhost" in w for w in detail["warnings"])


def test_no_success_wording_for_grade2(tmp_path, no_sleep, caplog):
    mina = StubMina()
    mina.status_state = "paused"
    sp = make_speaker(tmp_path, mina=mina)
    login(sp)
    with caplog.at_level(logging.INFO, logger="lit.xiaoai.speaker"):
        ok, msg, detail = sp.speak_with_detail("x")
    assert ok is True
    assert detail["grade"] == 2
    assert "成功" not in msg
    assert "成功" not in detail["message"]
    assert "成功" not in detail["grade_label"]
    assert "播报成功" not in caplog.text


def test_broadcast_record_full_elements(tmp_path, no_sleep, monkeypatch):
    _enable_local_tts(monkeypatch)
    _fast_clock(monkeypatch)
    mina = StubMina()
    sp = make_speaker(tmp_path, mina=mina)
    login(sp)
    ok, msg, detail = sp.speak_with_detail("第一节高数")
    assert ok is True
    records = diagnostics.get_broadcasts()
    assert len(records) == 1
    rec = records[0]
    assert rec["grade"] == 1
    assert rec["grade_label"] == "已确认播放"
    assert rec["channel"] == "primary"
    assert rec["device"] == {"device_id": "d1", "name": "小爱音箱",
                             "hardware": "LX06", "is_speaker": True}
    assert rec["synth"]["ok"] is True
    assert rec["synth"]["audio"] == "j.mp3"
    assert rec["observation"]["saw_playing"] is True
    assert rec["observation"]["last_state"] == "playing"
    assert rec["timestamp"]


def test_non_speaker_target_risk_flagged(tmp_path, no_sleep):
    mina = StubMina(devices=[ROBOT])
    sp = make_speaker(tmp_path, mina=mina)
    login(sp, devices=[ROBOT])
    ok, msg, detail = sp.speak_with_detail("x")
    assert ok is True
    assert len(mina.tts_calls) == 2  # 指令照常下发
    assert detail["device"]["is_speaker"] is False
    assert detail["device"]["hardware"] is None
    assert any("非音箱" in w for w in detail["warnings"])


def test_select_device_speaker_no_warning(tmp_path):
    sp = make_speaker(tmp_path)
    login(sp)
    result = sp.select_device("d1")
    assert result["device_id"] == "d1"
    assert result["is_speaker"] is True
    assert result["hardware"] == "LX06"
    assert result["warning"] is None


def test_select_device_non_speaker_warning(tmp_path):
    sp = make_speaker(tmp_path)
    login(sp, devices=[LAMP])
    result = sp.select_device("d2")
    assert result["is_speaker"] is False
    assert result["hardware"] == "MJCTD01"
    assert "非物理音箱" in result["warning"]


def test_play_url_direct_grade2_backup(tmp_path, no_sleep, monkeypatch):
    _fast_clock(monkeypatch)
    mina = StubMina()
    mina.status_state = "paused"
    sp = make_speaker(tmp_path, mina=mina)
    login(sp)
    ok, msg, detail = sp.play_url_direct("https://example.com/a.mp3")
    assert ok is True
    assert detail["channel"] == "backup"
    assert detail["grade"] == 2
    assert "成功" not in msg


def test_speak_with_result_thin_wrapper(tmp_path, no_sleep):
    mina = StubMina()
    sp = make_speaker(tmp_path, mina=mina)
    login(sp)
    ok2, msg2 = sp.speak_with_result("x")
    assert ok2 is True
    ok3, msg3, detail = sp.speak_with_detail("x")
    assert (ok2, msg2) == (ok3, msg3)
    assert ok3 is True