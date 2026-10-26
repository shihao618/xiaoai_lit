"""教务掉线自动恢复测试：重启/失效后自动重登、下周期重试、网络异常不误判。"""
import logging
import pathlib
from datetime import date

import requests

from lit.jwxt import api, state
from lit.jwxt.client import JWXTClient


def make_client(isolated_dirs):
    return JWXTClient(account="u1", password="pw1")


def _ctx():
    return api.term_code.TodayContext(
        xnxqdm="202601", zc=7, rq=date.today(), in_term=True)


def _mock_login_ok(monkeypatch, client, fetcher):
    monkeypatch.setattr("lit.jwxt.credentials.load_credentials", lambda: ("u1", "pw1"))
    monkeypatch.setattr(client, "get_captcha_image", lambda: b"fake-captcha")
    monkeypatch.setattr("lit.jwxt.api.login.recognize_captcha", lambda image: "abcd")
    monkeypatch.setattr("lit.jwxt.api.login.do_login",
                        lambda *a, **k: {"code": 0, "message": "ok"})
    monkeypatch.setattr("lit.jwxt.api.term_code.fetch_today_context", fetcher)


def test_restart_with_expired_cookie_triggers_relogin(isolated_dirs, monkeypatch, caplog):
    """重启后 Cookie 已在线程池外失效：探活失败 → 自动重登恢复并落盘。"""
    client = make_client(isolated_dirs)
    client.cookie_valid = True          # 启动时从 jwxt.json 加载了旧 Cookie
    client.logged_in = False
    calls = {"probe": 0}

    def fetcher(*a, **k):
        calls["probe"] += 1
        if calls["probe"] == 1:
            raise ValueError("学期代码查询失败: 未登录")
        return _ctx()

    _mock_login_ok(monkeypatch, client, fetcher)
    with caplog.at_level(logging.INFO):
        assert client.check_cookie_valid() is True
    assert client.logged_in is True
    assert client.cookie_valid is True
    assert "自动重登成功" in caplog.text
    assert state.read_state().get("account") == "u1"


def test_relogin_retries_next_cycle_not_sticky(isolated_dirs, monkeypatch):
    """自动重登失败后：下个检查周期必须再次尝试（旧实现 Cookie 失效标志粘住）。"""
    client = make_client(isolated_dirs)
    client.cookie_valid = False          # 已知失效（例如上个周期重登失败）
    monkeypatch.setattr("lit.jwxt.credentials.load_credentials", lambda: ("u1", "pw1"))
    login_calls = []

    def fake_do_login(*a, **k):
        login_calls.append(1)
        if len(login_calls) <= 2:        # 第一轮两次尝试都验证码错误
            return {"code": -1, "message": "验证码错误"}
        return {"code": 0, "message": "ok"}

    monkeypatch.setattr(client, "get_captcha_image", lambda: b"fake-captcha")
    monkeypatch.setattr("lit.jwxt.api.login.recognize_captcha", lambda image: "abcd")
    monkeypatch.setattr("lit.jwxt.api.login.do_login", fake_do_login)
    monkeypatch.setattr("lit.jwxt.api.term_code.fetch_today_context",
                        lambda *a, **k: _ctx())

    # 第一周期：2 次验证码尝试均失败 → 计入当日失败 1 次
    assert client.check_cookie_valid() is False
    assert len(login_calls) == 2
    assert client._relogin_failures_today == 1
    assert client._relogin_tried_this_cycle is True

    # 第二周期（30 分钟后调度器再次检查）：标志已重置，继续重试并成功
    assert client.check_cookie_valid() is True
    assert len(login_calls) == 3
    assert client.logged_in is True


def test_transient_network_does_not_invalidate_cookie(isolated_dirs, monkeypatch, caplog):
    """网络异常 → 不判会话失效、不消耗重登预算、不发登录请求。"""
    client = make_client(isolated_dirs)
    client.cookie_valid = True

    def boom(*a, **k):
        raise requests.ConnectionError("network down")

    login_calls = []

    def _no_login(*a, **k):
        login_calls.append(1)
        raise AssertionError("网络异常时不应触发登录")

    monkeypatch.setattr("lit.jwxt.api.term_code.fetch_today_context", boom)
    monkeypatch.setattr("lit.jwxt.api.login.do_login", _no_login)
    with caplog.at_level(logging.WARNING):
        assert client.check_cookie_valid() is False
    assert client.cookie_valid is True
    assert client._relogin_failures_today == 0
    assert login_calls == []
    assert "网络异常" in caplog.text


def test_get_course_table_recovers_after_restart(isolated_dirs, monkeypatch):
    """重启后首次刷新：探活失效 → 自动重登 → 继续取课表成功。"""
    client = make_client(isolated_dirs)
    client.cookie_valid = False
    client.logged_in = False
    week = [{"name": "高数", "date": date.today().isoformat(), "start_time": "08:00:00"}]
    probes = {"n": 0}

    def fetcher(*a, **k):
        probes["n"] += 1
        if probes["n"] == 1:
            raise ValueError("学期代码查询失败: 未登录")
        return _ctx()

    _mock_login_ok(monkeypatch, client, fetcher)
    monkeypatch.setattr("lit.jwxt.api.week_table.fetch_week_courses",
                        lambda *a, **k: [dict(c) for c in week])

    courses = client.get_course_table()
    assert courses == week
    assert client.logged_in is True
    assert client.cookie_valid is True


def test_manual_login_when_cookie_invalid(isolated_dirs, monkeypatch):
    """人工登录（Cookie 已知失效）不递归调用 check_cookie_valid。"""
    client = JWXTClient(account="u1", password="pw1")
    client.cookie_valid = False
    calls = []

    def fetcher(*a, **k):
        calls.append("fetch")
        return _ctx()

    monkeypatch.setattr(client, "get_captcha_image", lambda: b"fake-captcha")
    monkeypatch.setattr("lit.jwxt.api.login.recognize_captcha", lambda image: "abcd")
    monkeypatch.setattr("lit.jwxt.api.login.do_login",
                        lambda *a, **k: {"code": 0, "message": "ok"})
    monkeypatch.setattr("lit.jwxt.api.term_code.fetch_today_context", fetcher)
    assert client.login(verifycode="abcd")["success"] is True
    assert len(calls) == 1            # 仅登录后获取学期上下文一次


def test_restart_roundtrip_recovers_from_persisted_state(isolated_dirs, monkeypatch):
    """端到端：首次登录落盘 → 新实例（模拟容器重启）加载凭证/Cookie → 会话失效后自动重登。"""
    ctx = _ctx()
    monkeypatch.setattr("lit.jwxt.api.term_code.fetch_today_context", lambda *a, **k: ctx)
    monkeypatch.setattr("lit.jwxt.api.login.recognize_captcha", lambda image: "abcd")

    # 第一次运行：登录成功，凭证与 Cookie 落盘
    first = JWXTClient(account="u1", password="pw1")
    monkeypatch.setattr(first, "get_captcha_image", lambda: b"fake-captcha")
    monkeypatch.setattr("lit.jwxt.api.login.do_login",
                        lambda *a, **k: {"code": 0, "message": "ok"})
    assert first.login(verifycode="abcd")["success"] is True
    assert state.read_state().get("account") == "u1"
    assert (pathlib.Path(isolated_dirs["data"]) / "jwxt" / "credentials.json").exists()

    # 模拟容器重启：新实例从磁盘恢复账号、凭证、Cookie
    calls = {"probe": 0}

    def fetcher(*a, **k):
        calls["probe"] += 1
        if calls["probe"] == 1:
            raise ValueError("学期代码查询失败: 会话已失效")
        return ctx

    monkeypatch.setattr("lit.jwxt.api.term_code.fetch_today_context", fetcher)
    restarted = JWXTClient()
    assert restarted.account == "u1"
    assert restarted.cookie_valid is True       # Cookie 已从 jwxt.json 加载
    assert restarted.check_cookie_valid() is True
    assert restarted.logged_in is True
    assert calls["probe"] >= 2                  # 探活失败 → 自动重登 → 再取上下文
