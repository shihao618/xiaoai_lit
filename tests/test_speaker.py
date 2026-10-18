import json
from datetime import datetime

import pytest

from lit import config
from xiaoai import run_async
from xiaoai.account import PollResult
from xiaoai.credentials import CredentialStore, Credentials
from xiaoai.exceptions import (
    CommandRejectedError,
    CredentialRejectedError,
    NetworkError,
    RateLimitedError,
    RequestTimeoutError,
    XiaoAIError,
)
from xiaoai.mina import PlayerStatus, SpeakerDevice
from xiaoai.speaker import XiaoAISpeaker


class StubMina:
    def __init__(self, devices=None, tts_exc=None, url_exc=None, list_exc=None):
        self.devices = devices or []
        self.tts_calls = []
        self.url_calls = []
        self.stop_calls = []
        self.status_seq = []
        self.status_state = "playing"
        self.tts_exc = tts_exc
        self.url_exc = url_exc
        self.list_exc = list_exc
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
    monkeypatch.setattr("xiaoai.speaker.time.sleep", lambda s: None)


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
        "xiaoai.tts.synthesize_announcement",
        lambda prompt, text: ("j.mp3", 10.0))
    monkeypatch.setattr(
        "xiaoai.tts.audio_url",
        lambda f: "http://127.0.0.1:5000/audio/tts/" + f)
    monkeypatch.setattr(
        "xiaoai.tts.silence_url",
        lambda: "http://127.0.0.1:5000/audio/tts/silence30.mp3")


def _fast_clock(monkeypatch):
    t = [0.0]

    def fake_monotonic():
        t[0] += 2.0
        return t[0]

    monkeypatch.setattr("xiaoai.speaker.time.monotonic", fake_monotonic)


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
    ok, msg = sp.play_url_direct("https://example.com/a.mp3")
    assert ok is True
    assert mina.url_calls == [("d1", "https://example.com/a.mp3")]
    assert mina.stop_calls == ["d1"]  # 备份播放后 pause 防循环


def test_play_url_direct_not_logged_in(tmp_path):
    sp = make_speaker(tmp_path)
    ok, msg = sp.play_url_direct("https://example.com/a.mp3")
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


# ---------- 手动凭证两阶段 ----------

def test_manual_cookie_format_error(tmp_path):
    sp = make_speaker(tmp_path)
    with pytest.raises(XiaoAIError):
        sp.set_manual_cookie("foo=bar; baz=qux")
    assert sp.get_status()["state"] == "no_credential"


def test_manual_cookie_invalid_rollback(tmp_path):
    mina = StubMina(devices=[DEV])
    sp = make_speaker(tmp_path, mina=mina)
    login(sp)
    mina.list_exc = CredentialRejectedError("invalid")
    with pytest.raises(Exception):
        sp.set_manual_cookie("userId=new; serviceToken=bad", "user")
    assert sp.username == "42"
    assert sp.get_status()["state"] == "logged_in"
    data = json.loads(
        (tmp_path / "xiaoai_credentials.json").read_text(encoding="utf-8"))
    assert data["user_id"] == "42"


def test_manual_cookie_success(tmp_path):
    mina = StubMina(devices=[DEV])
    sp = make_speaker(tmp_path, mina=mina)
    assert sp.set_manual_cookie("userId=9; serviceToken=tok", "myname") is True
    assert sp.logged_in
    assert sp.username == "myname"
    status = sp.get_status()
    assert status["devices"][0]["deviceID"] == "d1"
    data = json.loads(
        (tmp_path / "xiaoai_credentials.json").read_text(encoding="utf-8"))
    assert data["user_id"] == "9"
    assert data["service_token"] == "tok"


def test_manual_token_camel_keys(tmp_path):
    mina = StubMina(devices=[DEV])
    sp = make_speaker(tmp_path, mina=mina)
    token_json = json.dumps(
        {"userId": "7", "deviceId": "dev7", "passToken": "v2:pt",
         "serviceToken": "st1", "ssecurity": "ss"})
    assert sp.set_manual_token(token_json) is True
    assert sp.get_status()["state"] == "logged_in"


def test_manual_token_snake_keys(tmp_path):
    mina = StubMina(devices=[DEV])
    sp = make_speaker(tmp_path, mina=mina)
    token_json = json.dumps(
        {"user_id": "7", "service_token": "st2"})
    assert sp.set_manual_token(token_json) is True


def test_manual_token_pass_token_only_triggers_exchange(tmp_path):
    class ExchangeAccount(StubAccount):
        def exchange(self, user_id, pass_token, device_id):
            result = super().exchange(user_id, pass_token, device_id)
            self.exchange_called = True
            return result

    account = ExchangeAccount()
    mina = StubMina(devices=[DEV])
    sp = make_speaker(tmp_path, mina=mina, account=account)
    token_json = json.dumps({"userId": "7", "passToken": "v2:pt"})
    assert sp.set_manual_token(token_json) is True
    assert getattr(account, "exchange_called", False) is True


def test_manual_token_broken_json(tmp_path):
    sp = make_speaker(tmp_path)
    with pytest.raises(XiaoAIError):
        sp.set_manual_token("{broken")
    with pytest.raises(XiaoAIError):
        sp.set_manual_token("[1,2]")


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
                          "hardware": "LX06", "address": "1.1.1.1"}
    assert devices[1]["name"] == "台灯"


def test_get_devices_70016_marks_rejected(tmp_path):
    mina = StubMina(list_exc=CredentialRejectedError("70016"))
    sp = make_speaker(tmp_path, mina=mina)
    login(sp)
    with pytest.raises(CredentialRejectedError):
        sp.get_devices()
    assert sp.get_status()["state"] == "rejected"