"""自动重登链路③测试（N4）：四重前置判定（锁定敬畏/凭证错误/无凭证/预算）+
成功恢复 + 失败分级 + 人工登录清标志（Q6）。

mock 点（design 2.6.4）：lit.jwxt.api.term_code.fetch_today_context /
lit.jwxt.api.login.do_login / lit.jwxt.credentials.load_credentials。
"""
import logging
from datetime import date, datetime, timedelta

from lit.jwxt import api
from lit.jwxt.client import JWXTClient, RELOGIN_DAILY_BUDGET


def make_client(isolated_dirs):
    return JWXTClient(account="u1", password="pw1")


def _ctx_today(in_term=True, zc=7, xnxqdm="202601"):
    return api.term_code.TodayContext(xnxqdm=xnxqdm, zc=zc, rq=date.today(), in_term=in_term)


def _mock_login_success(monkeypatch, client):
    monkeypatch.setattr("lit.jwxt.credentials.load_credentials", lambda: ("u1", "pw1"))
    monkeypatch.setattr(client, "get_captcha_image", lambda: b"fake-captcha")
    monkeypatch.setattr("lit.jwxt.api.login.recognize_captcha", lambda image: "abcd")
    monkeypatch.setattr("lit.jwxt.api.login.do_login",
                        lambda *a, **k: {"code": 0, "message": "ok"})
    monkeypatch.setattr("lit.jwxt.api.term_code.fetch_today_context",
                        lambda *a, **k: _ctx_today())


def test_auto_relogin_success(isolated_dirs, monkeypatch, caplog):
    """预算内恢复：登录成功 + 日志"自动重登成功"。"""
    client = make_client(isolated_dirs)
    _mock_login_success(monkeypatch, client)
    client.cookie_valid = False
    with caplog.at_level(logging.INFO):
        ok = client._try_auto_relogin()
    assert ok is True
    assert client.logged_in is True
    assert client.cookie_valid is True
    assert "自动重登成功" in caplog.text


def test_auto_relogin_no_credentials_skips(isolated_dirs, monkeypatch, caplog):
    """无凭证（持久化与环境变量均未配置）→ 跳过，零登录请求。"""
    client = make_client(isolated_dirs)
    monkeypatch.setattr("lit.jwxt.credentials.load_credentials", lambda: None)
    monkeypatch.setattr("lit.jwxt.client.config.JWXT_ACCOUNT", "")
    monkeypatch.setattr("lit.jwxt.client.config.JWXT_PASSWORD", "")

    def _boom(*a, **k):
        raise AssertionError("无凭证时不应发出登录请求")

    monkeypatch.setattr("lit.jwxt.api.login.do_login", _boom)
    with caplog.at_level(logging.INFO):
        assert client._try_auto_relogin() is False
    assert "无可用凭证" in caplog.text
    assert client.logged_in is False


def test_auto_relogin_locked_zero_requests(isolated_dirs, monkeypatch):
    """锁定期零请求（锁定敬畏）：session.get/post 均不应被调用。"""
    client = make_client(isolated_dirs)
    client._cooldown_until = datetime.now() + timedelta(minutes=10)
    calls = []

    def _boom(*a, **k):
        calls.append(1)
        raise AssertionError("锁定期不应发出任何请求")

    monkeypatch.setattr(client.session, "get", _boom)
    monkeypatch.setattr(client.session, "post", _boom)
    monkeypatch.setattr("lit.jwxt.api.login.do_login", _boom)
    assert client._try_auto_relogin() is False
    assert calls == []


def test_auto_relogin_credentials_error_stops(isolated_dirs, monkeypatch, caplog):
    """凭证错误 → _credentials_invalid=True 立即停止 + 告警（Q6 致命分级）。"""
    client = make_client(isolated_dirs)
    monkeypatch.setattr("lit.jwxt.credentials.load_credentials", lambda: ("u1", "wrong-pw"))
    monkeypatch.setattr(client, "get_captcha_image", lambda: b"fake-captcha")
    monkeypatch.setattr("lit.jwxt.api.login.recognize_captcha", lambda image: "abcd")
    login_calls = []

    def _fake_do_login(*a, **k):
        login_calls.append(1)
        return {"code": -1, "message": "账号或密码错误"}

    monkeypatch.setattr("lit.jwxt.api.login.do_login", _fake_do_login)
    client.cookie_valid = False
    with caplog.at_level(logging.INFO):
        assert client._try_auto_relogin() is False
    assert client._credentials_invalid is True
    assert "凭证疑似失效" in caplog.text
    assert len(login_calls) == 1
    # 再次触发：凭证错误标志拦截，零登录请求
    assert client._try_auto_relogin() is False
    assert len(login_calls) == 1


def test_auto_relogin_transient_failure_counts_and_cycle_once(isolated_dirs, monkeypatch, caplog):
    """临时失败计数 + 周期单次（同周期第二次跳过，不再发登录请求）。"""
    client = make_client(isolated_dirs)
    monkeypatch.setattr("lit.jwxt.credentials.load_credentials", lambda: ("u1", "pw1"))
    monkeypatch.setattr(client, "get_captcha_image", lambda: b"fake-captcha")
    monkeypatch.setattr("lit.jwxt.api.login.recognize_captcha", lambda image: "abcd")
    login_calls = []

    def _fake_do_login(*a, **k):
        login_calls.append(1)
        return {"code": -1, "message": "验证码错误"}

    monkeypatch.setattr("lit.jwxt.api.login.do_login", _fake_do_login)
    client.cookie_valid = False
    with caplog.at_level(logging.INFO):
        assert client._try_auto_relogin() is False
    assert client._relogin_failures_today == 1
    assert client._credentials_invalid is False
    assert "自动重登临时失败" in caplog.text
    # 同周期第二次：周期标志拦截，零登录请求（登录尝试次数 = RELOGIN_LOGIN_ATTEMPTS）
    assert client._try_auto_relogin() is False
    assert len(login_calls) == 2
    assert client._relogin_failures_today == 1


def test_auto_relogin_daily_budget_exhausted(isolated_dirs, monkeypatch, caplog):
    """日预算 5 次耗尽 → 静默跳过（零登录请求，次日日期翻转自动恢复）。"""
    client = make_client(isolated_dirs)
    client._relogin_failures_today = RELOGIN_DAILY_BUDGET
    client._relogin_failure_date = date.today()
    monkeypatch.setattr("lit.jwxt.credentials.load_credentials", lambda: ("u1", "pw1"))

    def _boom(*a, **k):
        raise AssertionError("预算耗尽后不应发出登录请求")

    monkeypatch.setattr("lit.jwxt.api.login.do_login", _boom)
    with caplog.at_level(logging.INFO):
        assert client._try_auto_relogin() is False
    assert "自动重登预算耗尽" in caplog.text


def test_auto_relogin_failure_feeds_lock_counter(isolated_dirs, monkeypatch):
    """失败累积 lock 计数（协同验证：login 失败 → _failed_attempts 递增）。"""
    client = make_client(isolated_dirs)
    monkeypatch.setattr("lit.jwxt.credentials.load_credentials", lambda: ("u1", "pw1"))
    monkeypatch.setattr(client, "get_captcha_image", lambda: b"fake-captcha")
    monkeypatch.setattr("lit.jwxt.api.login.recognize_captcha", lambda image: "abcd")
    monkeypatch.setattr("lit.jwxt.api.login.do_login",
                        lambda *a, **k: {"code": -1, "message": "验证码错误"})
    client.cookie_valid = False
    assert client._try_auto_relogin() is False
    # 验证码错误属临时性失败：不累计锁定计数（避免误触发冷却阻塞恢复）
    assert client._failed_attempts == 0


def test_manual_login_clears_credentials_invalid_flag(isolated_dirs, monkeypatch):
    """人工登录成功 → 清除凭证错误标志（唯一解除途径）。"""
    client = make_client(isolated_dirs)
    client._credentials_invalid = True
    monkeypatch.setattr("lit.jwxt.api.login.do_login",
                        lambda *a, **k: {"code": 0, "message": "ok"})
    monkeypatch.setattr("lit.jwxt.api.term_code.fetch_today_context",
                        lambda *a, **k: None)
    result = client.login(verifycode="wxyz")
    assert result["success"] is True
    assert client._credentials_invalid is False