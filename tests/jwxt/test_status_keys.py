"""/api/jwxt/status 9 键契约 + /api/status 零真实请求测试（jwxt_pure_api 契约 #2/#5）。
时钟基准：today 固定 2026-10-20（第 7 教学周），today_context 为 curMonthXnxq 内存态。"""
from datetime import date
from unittest.mock import MagicMock

import pytest
from flask import Flask

from lit.jwxt import api
from lit.jwxt.client import JWXTClient
from lit.web.blueprints.jwxt import create_jwxt_blueprint
from lit.web.blueprints.status import create_status_blueprint

XNXQDM = "20261"
TODAY = date(2026, 10, 20)

NINE_KEYS = {
    "logged_in", "cookie_valid", "login_time", "account", "locked",
    "lock_time", "failed_attempts", "xnxqdm", "current_week",
}


class _FakeDate(date):
    @classmethod
    def today(cls):
        return TODAY


@pytest.fixture
def env(monkeypatch, isolated_dirs):
    monkeypatch.setattr("lit.jwxt.client.date", _FakeDate)
    client = JWXTClient(account="u", password="p")
    client.xnxqdm = XNXQDM
    speaker = MagicMock()
    speaker.cookie_valid = False
    speaker.logged_in = False
    speaker.login_time = None
    speaker.username = "user"
    speaker.device_id = "device"
    app = Flask(__name__)
    app.register_blueprint(create_jwxt_blueprint(client))
    app.register_blueprint(create_status_blueprint(client, speaker))
    return app.test_client(), client


def _ctx(in_term=True, zc=7, xnxqdm=XNXQDM):
    return api.term_code.TodayContext(xnxqdm=xnxqdm, zc=zc, rq=TODAY, in_term=in_term)


def test_status_nine_keys_in_term(env):
    tc, client = env
    client.today_context = _ctx()
    body = tc.get("/api/jwxt/status").get_json()
    assert set(body.keys()) == NINE_KEYS
    assert body["logged_in"] is False
    assert body["cookie_valid"] is False
    assert body["login_time"] is None
    assert body["account"] == "u"
    assert body["locked"] is False
    assert body["lock_time"] is None
    assert body["failed_attempts"] == 0
    assert body["xnxqdm"] == XNXQDM
    assert body["current_week"] == 7


def test_status_nine_keys_out_of_term(env):
    tc, client = env
    client.today_context = _ctx(in_term=False, zc=0, xnxqdm="")
    body = tc.get("/api/jwxt/status").get_json()
    assert body["current_week"] is None
    assert len(body) == 9


def test_status_nine_keys_not_queried(env):
    tc, client = env
    client.today_context = None
    body = tc.get("/api/jwxt/status").get_json()
    assert body["current_week"] is None
    assert set(body.keys()) == NINE_KEYS


def test_api_status_zero_real_requests(env, monkeypatch):
    """/api/status 消费内存态 cookie_valid——零真实教务请求（HEALTHCHECK 复用前提）。"""
    tc, client = env
    calls = []

    def _boom(*args, **kwargs):
        calls.append(1)
        raise AssertionError("真实教务请求不应发出")

    monkeypatch.setattr(client.session, "get", _boom)
    monkeypatch.setattr(client.session, "post", _boom)
    r = tc.get("/api/status")
    assert r.status_code == 200
    body = r.get_json()
    assert body["jwxt"]["cookie_valid"] is False
    assert calls == []