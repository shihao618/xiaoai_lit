import base64

import time

import pytest

from lit.xiaoai import constants
from lit.xiaoai.account import PollResult
from lit.xiaoai.exceptions import QRSessionError, RateLimitedError
from lit.xiaoai.qrlogin import QRLoginManager, render_qr_png_data_uri


class StubAccount:
    def __init__(self, poll_results=None, exchange_result=None):
        self.poll_results = list(poll_results or [])
        self.poll_calls = 0
        self.exchange_result = exchange_result or {
            "user_id": "42", "pass_token": "pt", "device_id": "dev",
            "service_token": "st", "security": "sec"}
        self.exchanged = []

    def start_qr(self):
        return ("https://account.xiaomi.com/qr?ticket=t",
                "https://account.xiaomi.com/lp?x=1", "abc123dev4567890")

    def poll_qr(self, poll_url):
        self.poll_calls += 1
        if self.poll_results:
            item = self.poll_results.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return PollResult("waiting", next_poll_url=poll_url)

    def exchange(self, user_id, pass_token, device_id):
        self.exchanged.append((user_id, pass_token, device_id))
        if isinstance(self.exchange_result, Exception):
            raise self.exchange_result
        return self.exchange_result


def wait_terminal(session, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if session.snapshot()["status"] in ("confirmed", "expired", "failed"):
            return
        time.sleep(0.02)
    raise AssertionError(
        f"会话未到终态: {session.snapshot()}")


def test_confirmed_flow_with_devices():
    account = StubAccount(poll_results=[
        PollResult("waiting"),
        PollResult("confirmed", user_id="42", pass_token="pt"),
    ])
    devices_seen = []

    def on_confirmed(token):
        devices_seen.append(token)
        return [{"deviceID": "d1", "name": "小爱", "hardware": "LX06",
                 "address": "1.1.1.1"}]

    manager = QRLoginManager(account, on_confirmed)
    session = manager.start()
    wait_terminal(session)
    snap = session.snapshot()
    assert snap["status"] == "confirmed"
    assert snap["devices"][0]["deviceID"] == "d1"
    assert devices_seen[0]["service_token"] == "st"
    assert account.exchanged[0][0] == "42"
    assert len(account.exchanged[0][2]) == 16  # login_device_id

    result = manager.get_status(session.session_id)
    assert result["success"] is True
    assert result["status"] == "confirmed"
    assert result["devices"][0]["deviceID"] == "d1"

    state = manager.get_login_state()
    assert state["status"] == "success"
    assert state["devices"][0]["deviceID"] == "d1"


def test_expired_flow():
    account = StubAccount(poll_results=[PollResult("expired")])
    manager = QRLoginManager(account, lambda t: [])
    session = manager.start()
    wait_terminal(session)
    assert session.snapshot()["status"] == "expired"
    assert manager.get_status(session.session_id)["status"] == "expired"
    state = manager.get_login_state()
    assert state["status"] == "failed"
    assert "过期" in state["message"]


def test_exchange_rate_limited_marks_failed():
    account = StubAccount(poll_results=[
        PollResult("confirmed", user_id="42", pass_token="pt")])
    account.exchange_result = RateLimitedError("limited")
    manager = QRLoginManager(account, lambda t: [])
    session = manager.start()
    wait_terminal(session)
    snap = session.snapshot()
    assert snap["status"] == "failed"
    assert "频繁" in snap["message"]


def test_waiting_limited_then_expired_backoff(monkeypatch):
    monkeypatch.setattr(constants, "QR_LIMITED_RETRY_DELAY", 0.05)
    account = StubAccount(poll_results=[
        PollResult("waiting_limited"),
        PollResult("expired"),
    ])
    manager = QRLoginManager(account, lambda t: [])
    session = manager.start()
    wait_terminal(session)
    assert session.snapshot()["status"] == "expired"
    assert account.poll_calls == 2


def test_ttl_expiry_terminates_session(monkeypatch):
    monkeypatch.setattr(constants, "QR_SESSION_TTL_SECONDS", 0.2)
    account = StubAccount()
    manager = QRLoginManager(account, lambda t: [])
    session = manager.start()
    wait_terminal(session, timeout=5)
    assert session.snapshot()["status"] == "expired"


def test_new_session_invalidates_old():
    account = StubAccount()
    manager = QRLoginManager(account, lambda t: [])
    s1 = manager.start()
    assert manager.get_status(s1.session_id)["success"] is True
    s2 = manager.start()
    assert s1.cancel.is_set()
    with pytest.raises(QRSessionError):
        manager.get_status(s1.session_id)
    result = manager.get_status(s2.session_id)
    assert result["success"] is True
    assert result["status"] in ("waiting",)


def test_get_status_unknown_session():
    manager = QRLoginManager(StubAccount(), lambda t: [])
    with pytest.raises(QRSessionError):
        manager.get_status("nonexistent")


def test_get_login_state_idle_without_session():
    manager = QRLoginManager(StubAccount(), lambda t: [])
    state = manager.get_login_state()
    assert state["status"] == "idle"
    assert state["devices"] == []


def test_render_qr_png_data_uri():
    uri = render_qr_png_data_uri("https://account.xiaomi.com/qr?ticket=t")
    assert uri.startswith("data:image/png;base64,")
    import png
    raw = base64.b64decode(uri.split(",", 1)[1])
    assert raw.startswith(b"\x89PNG\r\n\x1a\n")
    width, height, _, _ = png.Reader(bytes=raw).read()
    assert width > 0 and width == height