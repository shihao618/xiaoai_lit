"""get_today_courses 三分支集成测试（tasks.md 3.4）：
monkeypatch client 模块 date.today + isolated_dirs 锚点构造 + mock get_course_table 固定课表。
真实锚点基准：第 1 教学周周一 = 2026-09-07；2026-10-20 周二 = 第 7 教学周（单周）。"""
import logging
from datetime import date, timedelta

import pytest

from lit.jwxt import state, term
from lit.jwxt.client import JWXTClient

XNXQDM = "20261"
ANCHOR = date(2026, 9, 7)

COURSES = [
    {"name": "单周课", "day": 2, "section_start": 1, "weeks": [1, 3, 5, 7, 9]},
    {"name": "双周课", "day": 2, "section_start": 3, "weeks": [2, 4, 6, 8, 10]},
    {"name": "每周课", "day": 2, "section_start": 2, "weeks": []},
    {"name": "旧缓存无weeks键", "day": 2, "section_start": 6},
    {"name": "周三课", "day": 3, "section_start": 1, "weeks": [7]},
]


class _FakeDate(date):
    """替换 client 模块命名空间的 date（term.py 显式传参不受影响）。"""
    _today = None

    @classmethod
    def today(cls):
        return cls._today


def _make_client(monkeypatch, today, courses=COURSES):
    _FakeDate._today = today
    monkeypatch.setattr("lit.jwxt.client.date", _FakeDate)
    client = JWXTClient(account="u", password="p")
    client.xnxqdm = XNXQDM
    monkeypatch.setattr(client, "get_course_table", lambda: courses)
    return client


def test_odd_week_filter_with_anchor(isolated_dirs, monkeypatch, caplog):
    """分支①：有效锚点第 7 单周——单周课/每周课/无weeks键入选，双周课与他日课排除。"""
    term.save_anchor(ANCHOR, XNXQDM, "manual")
    client = _make_client(monkeypatch, date(2026, 10, 20))
    with caplog.at_level(logging.INFO, logger="lit.jwxt.client"):
        result = client.get_today_courses()
    assert [c["name"] for c in result] == ["单周课", "每周课", "旧缓存无weeks键"]
    assert [c["section_start"] for c in result] == [1, 2, 6]
    assert "当前第 7 周" in caplog.text


def test_even_week_filter_with_anchor(isolated_dirs, monkeypatch, caplog):
    """分支①对称：第 8 双周——双周课入选、单周课排除，排序语义保持。"""
    term.save_anchor(ANCHOR, XNXQDM, "manual")
    client = _make_client(monkeypatch, date(2026, 10, 27))
    with caplog.at_level(logging.INFO, logger="lit.jwxt.client"):
        result = client.get_today_courses()
    assert [c["name"] for c in result] == ["每周课", "双周课", "旧缓存无weeks键"]
    assert [c["section_start"] for c in result] == [2, 3, 6]
    assert "当前第 8 周" in caplog.text


def test_no_anchor_fallback_day_only(isolated_dirs, monkeypatch, caplog):
    """分支③：锚点缺失回退仅按星期过滤（双周课亦入选）+ 告警日志。"""
    client = _make_client(monkeypatch, date(2026, 10, 20))
    with caplog.at_level(logging.WARNING, logger="lit.jwxt.client"):
        result = client.get_today_courses()
    assert [c["name"] for c in result] == ["单周课", "每周课", "双周课", "旧缓存无weeks键"]
    assert "学期锚点未生效，周次过滤未启用" in caplog.text


def test_out_of_term_returns_empty(isolated_dirs, monkeypatch, caplog):
    """分支②：学期外日期（锚点前一日，第 0 周）归空 + 日志。"""
    term.save_anchor(ANCHOR, XNXQDM, "manual")
    client = _make_client(monkeypatch, date(2026, 9, 6))
    with caplog.at_level(logging.INFO, logger="lit.jwxt.client"):
        result = client.get_today_courses()
    assert result == []
    assert "学期外日期" in caplog.text


def _raw_for_week(n):
    """构造第 n 教学周查询响应：2 正常票 + 1 调课污染离群票（实际在第 n+2 周上课）。"""
    monday = ANCHOR + timedelta(weeks=n - 1)
    return [
        {"qsrq": (monday + timedelta(days=1)).isoformat()},
        {"qsrq": (monday + timedelta(days=2)).isoformat()},
        {"qsrq": (monday + timedelta(weeks=2, days=1)).isoformat()},
    ]


def test_probe_success_then_filter_uses_new_anchor(isolated_dirs, monkeypatch, caplog):
    """探测链路：多数反推（2 正常票压 1 离群票）→ auto 落盘 → 当次判定采用新锚点。"""
    client = _make_client(monkeypatch, date(2026, 10, 20))
    monkeypatch.setattr(client, "_fetch_week_raw", lambda n: _raw_for_week(n))
    with caplog.at_level(logging.INFO, logger="lit.jwxt.client"):
        assert client.ensure_term_anchor(COURSES) is True
        assert "锚点探测成功" in caplog.text
        assert "票数 2/3" in caplog.text
    data = __import__("lit.jwxt.state", fromlist=["read_state"]).read_state()
    assert data["term_start_monday"] == "2026-09-07"
    assert data["term_xnxqdm"] == XNXQDM
    assert data["term_start_source"] == "auto"
    result = client.get_today_courses()
    assert [c["name"] for c in result] == ["单周课", "每周课", "旧缓存无weeks键"]


def test_ensure_anchor_skips_when_valid(isolated_dirs, monkeypatch):
    """幂等跳过：有效锚点存在 → ensure_term_anchor True 且零探测请求。"""
    term.save_anchor(ANCHOR, XNXQDM, "manual")
    client = _make_client(monkeypatch, date(2026, 10, 20))

    def _boom(n):
        raise AssertionError("有效锚点存在时不应发起探测请求")

    monkeypatch.setattr(client, "_fetch_week_raw", _boom)
    assert client.ensure_term_anchor(COURSES) is True