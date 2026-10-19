"""refresh_course_table 编排断言（tasks.md 4.2）：
先锚点保障后今日课程计算的时序 + 探测链路异常不阻断刷新主流程。
防御归属：client.ensure_term_anchor 全捕获（tasks.py 仅 +1 行，硬约束 6）。"""
from datetime import date
from unittest.mock import MagicMock

import requests

from lit.jwxt import state
from lit.jwxt.client import JWXTClient
from lit.tasks import BackgroundTasks

COURSES = [
    {"name": "高数", "day": 2, "section_start": 1, "section_end": 2,
     "start_time": "08:00", "classroom": "A101", "weeks": [7]},
]


def test_refresh_calls_anchor_before_today_courses():
    """触发时序断言：课表获取成功后先 ensure_term_anchor(courses) 后 get_today_courses()。"""
    jwxt = MagicMock()
    speaker = MagicMock()
    tasks = BackgroundTasks(jwxt, speaker)
    jwxt.get_course_table.return_value = COURSES
    calls = []
    jwxt.ensure_term_anchor.side_effect = lambda c: calls.append("anchor") or True
    jwxt.get_today_courses.side_effect = lambda: calls.append("today") or COURSES

    tasks.refresh_course_table()

    assert calls == ["anchor", "today"]
    jwxt.ensure_term_anchor.assert_called_once_with(COURSES)
    assert tasks.today_courses == COURSES


def test_probe_exception_does_not_block_refresh(isolated_dirs, monkeypatch, caplog):
    """探测链路网络异常：ensure_term_anchor 全捕获返回 False（零写入），
    今日课程照常计算（无锚点回退 day 过滤）+ 提醒注册不中断（spec 5.1.1 规则 5）。"""
    class _FakeDate(date):
        _today = date(2026, 10, 20)  # 周二，匹配 COURSES day=2（时钟防御，design 2.5 风险 12）

        @classmethod
        def today(cls):
            return cls._today

    monkeypatch.setattr("lit.jwxt.client.date", _FakeDate)
    client = JWXTClient(account="u", password="p")
    client.xnxqdm = "20261"
    monkeypatch.setattr(client, "get_course_table", lambda: COURSES)

    def _network_boom(n):
        raise requests.ConnectionError("network down")

    monkeypatch.setattr(client, "_fetch_week_raw", _network_boom)

    tasks = BackgroundTasks(client, MagicMock())
    tasks.refresh_course_table()

    data = state.read_state()
    assert not data  # 失败路径零写入（状态文件未创建）
    assert [c["name"] for c in tasks.today_courses] == ["高数"]  # 无锚点回退仍选中
    job_ids = [j.id for j in tasks.scheduler.get_jobs()]
    assert "remind_morning" in job_ids