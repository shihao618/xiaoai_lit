"""第 19 条路由 POST /api/jwxt/term_config + /api/jwxt/status 11 键契约测试（tasks.md 5.2）。
时钟基准：锚点 2026-09-07，today 固定 2026-10-20（周二，第 7 教学周）。"""
from datetime import date

import pytest
from flask import Flask

from lit.jwxt import state, term
from lit.jwxt.client import JWXTClient
from lit.web.blueprints.jwxt import create_jwxt_blueprint

XNXQDM = "20261"
ANCHOR = date(2026, 9, 7)
TODAY = date(2026, 10, 20)

COURSES = [
    {"name": "单周课", "day": 2, "section_start": 1, "weeks": [1, 3, 5, 7, 9]},
    {"name": "双周课", "day": 2, "section_start": 3, "weeks": [2, 4, 6, 8, 10]},
]


class _FakeDate(date):
    @classmethod
    def today(cls):
        return TODAY


@pytest.fixture
def env(monkeypatch, isolated_dirs):
    monkeypatch.setattr("lit.jwxt.client.date", _FakeDate)
    client = JWXTClient(account="u", password="p")
    client.xnxqdm = XNXQDM
    app = Flask(__name__)
    app.register_blueprint(create_jwxt_blueprint(client))
    return app.test_client(), client


def test_term_config_valid_monday_persists(env):
    tc, _ = env
    r = tc.post("/api/jwxt/term_config", json={"term_start_monday": "2026-09-07"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["success"] is True
    assert body["message"] == "学期锚点已设置: 2026-09-07"
    data = state.read_state()
    assert data["term_start_monday"] == "2026-09-07"
    assert data["term_xnxqdm"] == XNXQDM
    assert data["term_start_source"] == "manual"


def test_term_config_rejects_non_monday_keeps_existing(env):
    tc, _ = env
    term.save_anchor(ANCHOR, XNXQDM, "manual")
    r = tc.post("/api/jwxt/term_config", json={"term_start_monday": "2026-09-08"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["success"] is False
    assert "不是周一" in body["message"]
    data = state.read_state()
    assert data["term_start_monday"] == "2026-09-07"  # 既有锚点不变（规则 3a）


def test_term_config_rejects_bad_format(env):
    tc, _ = env
    r = tc.post("/api/jwxt/term_config", json={"term_start_monday": "2026/09/07"})
    body = r.get_json()
    assert body["success"] is False
    assert body["message"] == "日期格式非法，应为 YYYY-MM-DD"


def test_term_config_rejects_empty(env):
    tc, _ = env
    r = tc.post("/api/jwxt/term_config", json={})
    body = r.get_json()
    assert body["success"] is False
    assert body["message"] == "锚点日期不能为空"


def test_status_eleven_keys_with_anchor(env):
    tc, _ = env
    term.save_anchor(ANCHOR, XNXQDM, "manual")
    r = tc.get("/api/jwxt/status")
    body = r.get_json()
    assert set(body.keys()) == {
        "logged_in", "cookie_valid", "login_time", "account", "locked",
        "lock_time", "failed_attempts", "xnxqdm",
        "current_week", "term_start_monday", "week_filter_enabled",
    }
    assert body["logged_in"] is False
    assert body["cookie_valid"] is False
    assert body["login_time"] is None
    assert body["account"] == "u"
    assert body["locked"] is False
    assert body["lock_time"] is None
    assert body["failed_attempts"] == 0
    assert body["xnxqdm"] == XNXQDM
    assert body["current_week"] == 7
    assert body["term_start_monday"] == "2026-09-07"
    assert body["week_filter_enabled"] is True


def test_status_eleven_keys_without_anchor(env):
    tc, _ = env
    r = tc.get("/api/jwxt/status")
    body = r.get_json()
    assert body["current_week"] is None
    assert body["term_start_monday"] is None
    assert body["week_filter_enabled"] is False
    assert len(body) == 11  # 既有 8 键仍在


def test_manual_overrides_auto_source(env):
    tc, _ = env
    term.save_anchor(ANCHOR, XNXQDM, "auto")
    r = tc.post("/api/jwxt/term_config", json={"term_start_monday": "2026-09-07"})
    assert r.get_json()["success"] is True
    assert state.read_state()["term_start_source"] == "manual"


def test_term_switch_invalidates_anchor(env, monkeypatch):
    tc, client = env
    term.save_anchor(ANCHOR, XNXQDM, "manual")
    state.update_state({"term_xnxqdm": "20262"})  # 模拟学期切换
    body = tc.get("/api/jwxt/status").get_json()
    assert body["current_week"] is None
    assert body["term_start_monday"] is None
    assert body["week_filter_enabled"] is False
    monkeypatch.setattr(client, "get_course_table", lambda: COURSES)
    result = client.get_today_courses()  # 回退仅按星期过滤（双周课亦入选）
    assert [c["name"] for c in result] == ["单周课", "双周课"]