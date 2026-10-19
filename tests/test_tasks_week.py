"""refresh_course_table 编排断言（5.11，D4 + N7）：
新编排时序（get_course_table 单调用内聚定位+查询，无独立锚点调用）+
定位/查询链路网络异常不阻断刷新主流程（防御归属 client 全捕获降级）+
提醒注册 job 时刻（CronTrigger hour/minute 断言）。"""
from datetime import date
from unittest.mock import MagicMock

import requests

from lit.jwxt import state
from lit.jwxt.api.week_table import save_cache
from lit.jwxt.client import JWXTClient
from lit.tasks import BackgroundTasks

TODAY = date(2026, 10, 20)  # 周二（时钟防御，design 2.5 风险 12）

COURSES = [
    {"name": "高数", "section_start": 1, "section_end": 2, "day": 2,
     "date": "2026-10-20", "start_time": "08:00:00", "end_time": "09:40:00",
     "classroom": "A101", "teacher": "教师A"},
]


class _FakeDate(date):
    @classmethod
    def today(cls):
        return TODAY


def test_refresh_chain_order():
    """触发时序断言：课表获取（单调用内聚）成功后 get_today_courses + 提醒注册。"""
    jwxt = MagicMock()
    calls = []
    jwxt.get_course_table.side_effect = lambda: calls.append("table") or COURSES
    jwxt.get_today_courses.side_effect = lambda: calls.append("today") or COURSES

    tasks = BackgroundTasks(jwxt, MagicMock())
    tasks.refresh_course_table()

    assert calls == ["table", "today"]
    jwxt.get_course_table.assert_called_once()
    assert tasks.today_courses == COURSES


def test_locator_and_query_failure_does_not_block_refresh(isolated_dirs, monkeypatch, caplog):
    """定位/查询链路网络异常：client 全捕获降级（缓存回退），刷新主流程不中断
    （零 state 写入 + 回退过滤 + 提醒注册不中断——宁可多提醒集成验证）。"""
    monkeypatch.setattr("lit.jwxt.client.date", _FakeDate)
    save_cache("202601", 7, date(2026, 10, 19), date(2026, 10, 25),
               [dict(c) for c in COURSES])

    def _net_boom(*a, **k):
        raise requests.ConnectionError("network down")

    monkeypatch.setattr("lit.jwxt.api.term_code.fetch_today_context", _net_boom)
    monkeypatch.setattr("lit.jwxt.api.week_table.fetch_week_courses", _net_boom)

    client = JWXTClient(account="u1", password="pw1")
    client.logged_in = True
    tasks = BackgroundTasks(client, MagicMock())
    tasks.refresh_course_table()

    data = state.read_state()
    assert not data  # 降级路径零 state 写入（xnxqdm 域未被触碰）
    assert [c["name"] for c in tasks.today_courses] == ["高数"]  # 缓存回退 date 过滤
    job_ids = [j.id for j in tasks.scheduler.get_jobs()]
    assert "remind_morning" in job_ids  # 提醒注册不中断


def test_orchestration_single_converged_call():
    """编排单调用断言（N7）：get_course_table 内聚定位+查询，无独立锚点调用。"""
    jwxt = MagicMock()
    jwxt.get_course_table.side_effect = lambda: COURSES
    jwxt.get_today_courses.side_effect = lambda: COURSES
    tasks = BackgroundTasks(jwxt, MagicMock())
    tasks.refresh_course_table()
    called = [name for name, args, kwargs in jwxt.method_calls]
    assert "get_course_table" in called
    assert "fetch_term_anchor" not in called  # 独立锚点调用已不存在
    jwxt.get_course_table.assert_called_once()


def test_remind_job_cron_time(monkeypatch):
    """提醒注册 job 时刻（N7）：最早课 08:00 → CronTrigger 07:30。"""
    monkeypatch.setattr("lit.tasks.config.REMIND_BEFORE_MINUTES", 30)
    tasks = BackgroundTasks(MagicMock(), MagicMock())
    tasks.today_courses = [dict(c) for c in COURSES]
    tasks._schedule_reminders()
    trigger = tasks.scheduler.get_job("remind_morning").trigger
    fields = {f.name: str(f) for f in trigger.fields}
    assert fields["hour"] == "7"
    assert fields["minute"] == "30"