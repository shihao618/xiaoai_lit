"""锁定判定组：_parse_lock_time / is_locked / 失败流转 / 持久化往返（spec 5.2.1 规则 5）。"""
import json
import os
from datetime import datetime, timedelta

import jwxt
from jwxt import COOLDOWN_MINUTES, MAX_FAILED_ATTEMPTS, JWXTClient


class StubResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def make_client(isolated_dirs):
    return JWXTClient(account="u1", password="p1")


def test_constants_frozen():
    assert MAX_FAILED_ATTEMPTS == 3
    assert COOLDOWN_MINUTES == 30


def test_parse_lock_time_standard(isolated_dirs):
    client = make_client(isolated_dirs)
    t = client._parse_lock_time("账号已锁定，解锁时间：2026-10-18 15:30:00 请稍后")
    assert t == datetime(2026, 10, 18, 15, 30, 0)


def test_parse_lock_time_absent(isolated_dirs):
    client = make_client(isolated_dirs)
    assert client._parse_lock_time("密码错误") is None


def test_parse_lock_time_malformed(isolated_dirs):
    client = make_client(isolated_dirs)
    assert client._parse_lock_time("锁定至 2026-13-45 99:99:99") is None


def test_is_locked_future_lock_until(isolated_dirs):
    client = make_client(isolated_dirs)
    client._lock_until = datetime.now() + timedelta(hours=1)
    locked, t = client.is_locked()
    assert locked is True
    assert t == client._lock_until


def test_is_locked_future_cooldown(isolated_dirs):
    client = make_client(isolated_dirs)
    client._cooldown_until = datetime.now() + timedelta(minutes=10)
    locked, t = client.is_locked()
    assert locked is True
    assert t == client._cooldown_until


def test_is_locked_expired_returns_false(isolated_dirs):
    client = make_client(isolated_dirs)
    client._lock_until = datetime.now() - timedelta(minutes=1)
    client._cooldown_until = datetime.now() - timedelta(minutes=1)
    assert client.is_locked() == (False, None)


def _drive_failures(client, monkeypatch, message, attempts):
    monkeypatch.setattr(client, "_get_captcha_ocr", lambda: "abcd")
    monkeypatch.setattr(client.session, "post",
                        lambda *a, **k: StubResponse({"code": -1, "message": message}))
    return client.login(verifycode="wxyz", max_retries=attempts)


def test_login_three_failures_enter_cooldown(isolated_dirs, monkeypatch):
    client = make_client(isolated_dirs)
    before = datetime.now()
    result = _drive_failures(client, monkeypatch, "密码错误", MAX_FAILED_ATTEMPTS)
    assert result["success"] is False
    assert result["locked"] is True
    assert client._failed_attempts == MAX_FAILED_ATTEMPTS
    assert client._cooldown_until is not None
    assert client._cooldown_until - before >= timedelta(minutes=COOLDOWN_MINUTES)
    assert "30" in result["message"]


def test_login_lock_message_takes_priority(isolated_dirs, monkeypatch):
    client = make_client(isolated_dirs)
    result = _drive_failures(
        client, monkeypatch, "账号已锁定，解锁时间：2026-10-18 15:00:00", 1)
    assert result["success"] is False
    assert result["locked"] is True
    assert client._lock_until == datetime(2026, 10, 18, 15, 0, 0)
    assert client._cooldown_until is None
    assert result["lock_time"] == "2026-10-18T15:00:00"


def test_lock_persistence_roundtrip(isolated_dirs, monkeypatch):
    client = make_client(isolated_dirs)
    _drive_failures(client, monkeypatch, "密码错误", MAX_FAILED_ATTEMPTS)
    assert os.path.exists(os.path.join(isolated_dirs["data"], "jwxt_lock.json"))
    restored = JWXTClient(account="u1", password="p1")
    assert restored._failed_attempts == MAX_FAILED_ATTEMPTS
    locked, _ = restored.is_locked()
    assert locked is True


def test_lock_persistence_account_mismatch(isolated_dirs, monkeypatch):
    client = make_client(isolated_dirs)
    _drive_failures(client, monkeypatch, "密码错误", MAX_FAILED_ATTEMPTS)
    other = JWXTClient(account="u2", password="p1")
    assert other._failed_attempts == 0
    assert other.is_locked() == (False, None)


def test_lock_persistence_expired_not_restored(isolated_dirs, monkeypatch):
    client = make_client(isolated_dirs)
    client._lock_until = datetime.now() - timedelta(minutes=5)
    client._failed_attempts = 2
    client._save_lock()
    restored = JWXTClient(account="u1", password="p1")
    assert restored._lock_until is None
    assert restored.is_locked() == (False, None)


def test_clear_lock_removes_file(isolated_dirs, monkeypatch):
    client = make_client(isolated_dirs)
    _drive_failures(client, monkeypatch, "密码错误", MAX_FAILED_ATTEMPTS)
    lock_file = os.path.join(isolated_dirs["data"], "jwxt_lock.json")
    assert os.path.exists(lock_file)
    client._clear_lock()
    assert not os.path.exists(lock_file)
    assert client._failed_attempts == 0