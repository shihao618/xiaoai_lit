"""刷新链路①/④测试（N5）：主干全序 / 学期外（F3）/ F1 兜底 / F4 缓存回退窗口 ×2 /
F10 跨周弃用（Q10 方向）/ F14 学期切换 / F5 空当周 / 提醒注册 qssj 时刻 ×3。

时钟基准：today 固定 2026-10-20（周二，第 7 教学周）；mock 点
lit.jwxt.api.term_code.fetch_today_context + lit.jwxt.api.week_table.fetch_week_courses。
"""
import logging
from datetime import date
from unittest.mock import MagicMock

import requests

from lit.jwxt import api, state
from lit.jwxt.api.term_code import TodayContext
from lit.jwxt.client import JWXTClient
from lit.tasks import BackgroundTasks

TODAY = date(2026, 10, 20)

COURSE_TUE = {"name": "高数", "section_start": 1, "section_end": 2, "day": 2,
              "date": "2026-10-20", "start_time": "08:00:00", "end_time": "09:40:00",
              "classroom": "A101", "teacher": "教师A"}
COURSE_WED = {"name": "大学英语", "section_start": 3, "section_end": 4, "day": 3,
              "date": "2026-10-21", "start_time": "14:30:00", "end_time": "16:10:00",
              "classroom": "B202", "teacher": "教师B"}
WEEK_COURSES = [COURSE_TUE, COURSE_WED]


class _FakeDate(date):
    @classmethod
    def today(cls):
        return TODAY


def _client(isolated_dirs, monkeypatch):
    monkeypatch.setattr("lit.jwxt.client.date", _FakeDate)
    client = JWXTClient(account="u1", password="pw1")
    client.logged_in = True
    return client


def _ctx(in_term=True, zc=7, xnxqdm="202601"):
    return TodayContext(xnxqdm=xnxqdm, zc=zc, rq=TODAY, in_term=in_term)


def _net_boom(*a, **k):
    raise requests.ConnectionError("network down")


def test_refresh_chain_main_sequence(isolated_dirs, monkeypatch):
    """主干全序：定位（curMonthXnxq）→ 查询（zc=N）→ 五元组缓存 → 当日过滤。"""
    order = []

    def fake_ctx(session, base_url, today):
        order.append("locate")
        assert today == TODAY
        return _ctx()

    def fake_week(session, base_url, xnxqdm, zc):
        order.append("query")
        assert (xnxqdm, zc) == ("202601", 7)
        return [dict(c) for c in WEEK_COURSES]

    monkeypatch.setattr("lit.jwxt.api.term_code.fetch_today_context", fake_ctx)
    monkeypatch.setattr("lit.jwxt.api.week_table.fetch_week_courses", fake_week)
    client = _client(isolated_dirs, monkeypatch)

    courses = client.get_course_table()
    assert order == ["locate", "query"]
    assert courses == WEEK_COURSES
    data = api.week_table.load_cache()
    assert (data["xnxqdm"], data["zc"]) == ("202601", 7)
    assert (data["week_start"], data["week_end"]) == ("2026-10-19", "2026-10-25")
    assert data["courses"] == WEEK_COURSES
    assert client.get_today_courses() == [COURSE_TUE]
    assert client._week_courses_date == "2026-10-20"


def test_refresh_out_of_term_f3(isolated_dirs, monkeypatch, caplog):
    """学期外分支（F3 正常分支）：无课 + 不注册提醒 + 徽章语义源（current_week None）。"""
    monkeypatch.setattr("lit.jwxt.api.term_code.fetch_today_context",
                        lambda *a, **k: _ctx(in_term=False, zc=0, xnxqdm=""))
    monkeypatch.setattr("lit.jwxt.api.week_table.fetch_week_courses",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("学期外不应查询周课表")))
    client = _client(isolated_dirs, monkeypatch)
    with caplog.at_level(logging.INFO):
        assert client.get_course_table() == []
    assert "学期外" in caplog.text
    assert client.get_status()["current_week"] is None

    tasks = BackgroundTasks(client, MagicMock())
    tasks.refresh_course_table()
    assert tasks.today_courses == []
    remind_ids = [j.id for j in tasks.scheduler.get_jobs() if j.id.startswith("remind_")]
    assert remind_ids == []


def test_refresh_f1_network_failure_uses_cached_context(isolated_dirs, monkeypatch, caplog):
    """F1：curMonthXnxq 网络异常 → 内存态今日上下文兜底，周课表仍以缓存 xnxqdm 尝试。"""
    week_calls = []

    def fake_week(session, base_url, xnxqdm, zc):
        week_calls.append((xnxqdm, zc))
        return [dict(COURSE_TUE)]

    monkeypatch.setattr("lit.jwxt.api.term_code.fetch_today_context", _net_boom)
    monkeypatch.setattr("lit.jwxt.api.week_table.fetch_week_courses", fake_week)
    client = _client(isolated_dirs, monkeypatch)
    client.today_context = _ctx()
    with caplog.at_level(logging.INFO):
        courses = client.get_course_table()
    assert courses == [COURSE_TUE]
    assert week_calls == [("202601", 7)]
    assert "降级使用缓存周次" in caplog.text


def test_refresh_f4_cache_fallback_covered(isolated_dirs, monkeypatch, caplog):
    """F4：周课表查询失败 → 缓存回退（窗口覆盖 + 元数据一致）→ 当日课程按缓存 date 过滤。"""
    api.week_table.save_cache("202601", 7, date(2026, 10, 19), date(2026, 10, 25),
                              [dict(c) for c in WEEK_COURSES])
    monkeypatch.setattr("lit.jwxt.api.term_code.fetch_today_context",
                        lambda *a, **k: _ctx())
    monkeypatch.setattr("lit.jwxt.api.week_table.fetch_week_courses", _net_boom)
    client = _client(isolated_dirs, monkeypatch)
    with caplog.at_level(logging.INFO):
        courses = client.get_course_table()
    assert courses == WEEK_COURSES
    assert "周课表查询失败，使用缓存" in caplog.text
    assert client.get_today_courses() == [COURSE_TUE]


def test_refresh_f4_cache_fallback_not_covered(isolated_dirs, monkeypatch):
    """F4：缓存窗口不覆盖今日 → 回退失败 → None（课表数据不可用）。"""
    api.week_table.save_cache("202601", 7, date(2026, 10, 12), date(2026, 10, 18),
                              [dict(COURSE_TUE)])
    monkeypatch.setattr("lit.jwxt.api.term_code.fetch_today_context",
                        lambda *a, **k: _ctx())
    monkeypatch.setattr("lit.jwxt.api.week_table.fetch_week_courses", _net_boom)
    client = _client(isolated_dirs, monkeypatch)
    assert client.get_course_table() is None


def test_refresh_f10_cross_week_cache_discarded(isolated_dirs, monkeypatch, caplog):
    """F10：缓存跨周弃用（Q10 方向验证：不拿上周当本周）——窗口覆盖但 zc 不一致。"""
    api.week_table.save_cache("202601", 6, date(2026, 10, 19), date(2026, 10, 25),
                              [dict(COURSE_TUE)])
    monkeypatch.setattr("lit.jwxt.api.term_code.fetch_today_context",
                        lambda *a, **k: _ctx())
    monkeypatch.setattr("lit.jwxt.api.week_table.fetch_week_courses", _net_boom)
    client = _client(isolated_dirs, monkeypatch)
    with caplog.at_level(logging.INFO):
        assert client.get_course_table() is None
    assert "课表数据不可用" in caplog.text


def test_refresh_f14_term_switch(isolated_dirs, monkeypatch, caplog):
    """F14 学期切换链：xnxqdm 变化 → 旧缓存弃用 → 新学期 zc=1 重查 + 日志"学期切换"。"""
    client = _client(isolated_dirs, monkeypatch)
    client.xnxqdm = "202601"
    api.week_table.save_cache("202601", 7, date(2026, 10, 19), date(2026, 10, 25),
                              [dict(COURSE_TUE)])
    new_course = {"name": "新学期课", "section_start": 1, "section_end": 2, "day": 2,
                  "date": "2026-10-20", "start_time": "08:00:00", "end_time": "09:40:00",
                  "classroom": "C303", "teacher": "教师C"}
    week_calls = []

    def fake_week(session, base_url, xnxqdm, zc):
        week_calls.append((xnxqdm, zc))
        return [dict(new_course)]

    monkeypatch.setattr("lit.jwxt.api.term_code.fetch_today_context",
                        lambda *a, **k: _ctx(zc=1, xnxqdm="202701"))
    monkeypatch.setattr("lit.jwxt.api.week_table.fetch_week_courses", fake_week)
    with caplog.at_level(logging.INFO):
        courses = client.get_course_table()
    assert "学期切换" in caplog.text
    assert client.xnxqdm == "202701"
    assert week_calls == [("202701", 1)]
    assert state.read_state()["xnxqdm"] == "202701"
    data = api.week_table.load_cache()
    assert data["xnxqdm"] == "202701"
    assert courses == [new_course]


def test_refresh_f5_empty_week(isolated_dirs, monkeypatch, caplog):
    """F5：当周空课程（正常分支）——空列表合法 + 缓存落盘空当周 + 无提醒。"""
    monkeypatch.setattr("lit.jwxt.api.term_code.fetch_today_context",
                        lambda *a, **k: _ctx())
    monkeypatch.setattr("lit.jwxt.api.week_table.fetch_week_courses",
                        lambda *a, **k: [])
    client = _client(isolated_dirs, monkeypatch)
    with caplog.at_level(logging.INFO):
        assert client.get_course_table() == []
    assert "本教学周无课程" in caplog.text
    data = api.week_table.load_cache()
    assert data["courses"] == []
    assert data["zc"] == 7
    tasks = BackgroundTasks(client, MagicMock())
    tasks.refresh_course_table()
    assert tasks.today_courses == []
    assert [j.id for j in tasks.scheduler.get_jobs() if j.id.startswith("remind_")] == []


def _make_tasks(monkeypatch):
    monkeypatch.setattr("lit.tasks.config.REMIND_BEFORE_MINUTES", 30)
    return BackgroundTasks(MagicMock(), MagicMock())


def _cron_field(scheduler, job_id, name):
    trigger = scheduler.get_job(job_id).trigger
    for f in trigger.fields:
        if f.name == name:
            return str(f)
    raise KeyError(name)


def test_remind_schedule_morning_0800_to_0730(monkeypatch):
    """上午最早课 08:00 → 提醒 07:30（REMIND_BEFORE_MINUTES=30 等价性）。"""
    tasks = _make_tasks(monkeypatch)
    tasks.today_courses = [dict(COURSE_TUE)]
    tasks._schedule_reminders()
    assert _cron_field(tasks.scheduler, "remind_morning", "hour") == "7"
    assert _cron_field(tasks.scheduler, "remind_morning", "minute") == "30"
    assert tasks.scheduler.get_job("remind_afternoon") is None


def test_remind_schedule_afternoon_1430_to_1400(monkeypatch):
    """下午最早课 14:30 → 提醒 14:00。"""
    tasks = _make_tasks(monkeypatch)
    tasks.today_courses = [dict(COURSE_WED)]
    tasks._schedule_reminders()
    assert _cron_field(tasks.scheduler, "remind_afternoon", "hour") == "14"
    assert _cron_field(tasks.scheduler, "remind_afternoon", "minute") == "0"
    assert tasks.scheduler.get_job("remind_morning") is None


def test_remind_schedule_min_of_multiple_courses(monkeypatch):
    """min 多课取最早：上午 08:00/10:10 → 07:30；下午 14:30/16:00 → 14:00。"""
    tasks = _make_tasks(monkeypatch)
    tasks.today_courses = [
        dict(COURSE_TUE),
        {"name": "物理", "section_start": 3, "section_end": 4, "day": 2,
         "date": "2026-10-20", "start_time": "10:10:00", "end_time": "11:50:00",
         "classroom": "A102", "teacher": "教师A"},
        dict(COURSE_WED),
        {"name": "体育", "section_start": 5, "section_end": 6, "day": 2,
         "date": "2026-10-20", "start_time": "16:00:00", "end_time": "17:40:00",
         "classroom": "操场", "teacher": "教师B"},
    ]
    tasks._schedule_reminders()
    assert _cron_field(tasks.scheduler, "remind_morning", "hour") == "7"
    assert _cron_field(tasks.scheduler, "remind_morning", "minute") == "30"
    assert _cron_field(tasks.scheduler, "remind_afternoon", "hour") == "14"
    assert _cron_field(tasks.scheduler, "remind_afternoon", "minute") == "0"