"""学期域纯函数组：锚点存取/周次计算/双条件过滤/校历网格解析（calendar_api_simplify design 2.7 冻结行为）。"""
import json
import logging
import os
from datetime import date, timedelta

from lit.jwxt import state, term
from lit.jwxt.client import JWXTClient


def read_state_json(isolated_dirs):
    with open(state.state_path(), "r", encoding="utf-8") as f:
        return json.load(f)


def write_anchor(isolated_dirs, monday, xnxqdm, source="auto"):
    os.makedirs(os.path.join(isolated_dirs["data"], "jwxt"), exist_ok=True)
    state.update_state({
        "term_start_monday": monday,
        "term_xnxqdm": xnxqdm,
        "term_start_source": source,
    })


ANCHOR = date(2026, 9, 7)


def test_current_week_boundaries():
    assert term.current_week(ANCHOR, ANCHOR) == 1
    assert term.current_week(ANCHOR, ANCHOR + __import__("datetime").timedelta(days=6)) == 1
    assert term.current_week(ANCHOR, ANCHOR + __import__("datetime").timedelta(days=7)) == 2
    assert term.current_week(ANCHOR, ANCHOR - __import__("datetime").timedelta(days=1)) == 0


def test_parse_anchor_valid_monday():
    assert term.parse_anchor("2026-09-07") == ANCHOR


def test_parse_anchor_non_monday():
    assert term.parse_anchor("2026-09-08") is None
    assert term.parse_anchor("2026-09-06") is None


def test_parse_anchor_invalid_format():
    assert term.parse_anchor("2026/09/07") is None
    assert term.parse_anchor("not-a-date") is None


def test_parse_anchor_empty():
    assert term.parse_anchor("") is None
    assert term.parse_anchor(None) is None


def test_get_effective_anchor_unconfigured(isolated_dirs):
    assert term.get_effective_anchor("202601") is None


def test_get_effective_anchor_invalid_value(isolated_dirs):
    write_anchor(isolated_dirs, "2026-09-08", "202601")
    assert term.get_effective_anchor("202601") is None


def test_get_effective_anchor_term_mismatch(isolated_dirs):
    write_anchor(isolated_dirs, "2026-09-07", "202601")
    assert term.get_effective_anchor("202602") is None


def test_get_effective_anchor_valid_pair(isolated_dirs):
    write_anchor(isolated_dirs, "2026-09-07", "202601")
    assert term.get_effective_anchor("202601") == ANCHOR


def test_save_anchor_pairs_and_preserves(isolated_dirs):
    state.update_state({"cookies": {"JSESSIONID": "x"}, "account": "u1"})
    term.save_anchor(ANCHOR, "202601", "manual")
    data = read_state_json(isolated_dirs)
    assert data["term_start_monday"] == "2026-09-07"
    assert data["term_xnxqdm"] == "202601"
    assert data["term_start_source"] == "manual"
    assert data["cookies"] == {"JSESSIONID": "x"}
    assert data["account"] == "u1"
    assert data["version"] == 1


def test_filter_today_odd_course(isolated_dirs):
    courses = [{"name": "a", "day": 1, "weeks": [1, 3, 5, 7], "section_start": 1}]
    assert term.filter_today(courses, 1, 7) == courses
    assert term.filter_today(courses, 1, 8) == []


def test_filter_today_even_course():
    courses = [{"name": "b", "day": 2, "weeks": [2, 4, 6, 8], "section_start": 3}]
    assert term.filter_today(courses, 2, 8) == courses
    assert term.filter_today(courses, 2, 7) == []


def test_filter_today_empty_weeks_always():
    courses = [{"name": "c", "day": 3, "weeks": [], "section_start": 5}]
    for week in (1, 7, 20):
        assert term.filter_today(courses, 3, week) == courses


def test_filter_today_partial_weeks():
    courses = [{"name": "d", "day": 4, "weeks": [5, 6, 7, 8], "section_start": 1}]
    assert term.filter_today(courses, 4, 6) == courses
    assert term.filter_today(courses, 4, 4) == []
    assert term.filter_today(courses, 4, 9) == []


def test_filter_today_missing_weeks_key():
    courses = [{"name": "e", "day": 5, "section_start": 1}]
    assert term.filter_today(courses, 5, 7) == courses
    assert term.filter_today(courses, 4, 7) == []


def test_filter_today_day_mismatch():
    courses = [{"name": "f", "day": 1, "weeks": [7], "section_start": 1}]
    assert term.filter_today(courses, 2, 7) == []


def _cal_head():
    return [{"type": "head", "value": "周次"}] + [
        {"type": "xq", "value": v, "fullText": f}
        for v, f in (("一", "星期一"), ("二", "星期二"), ("三", "星期三"), ("四", "星期四"),
                     ("五", "星期五"), ("六", "星期六"), ("日", "星期日"))
    ]


def _cal_day(d):
    return {"type": "day", "value": str(d.day), "fullText": d.isoformat(),
            "month": f"{d.year}-{d.month:02d}", "zt": "1", "bz": "", "dm": "0"}


def _cal_week(n, start, empty_leading=0):
    row = [{"type": "zc", "value": str(n), "fullText": f"第{n}周"}]
    row += [{"type": "day", "value": ""} for _ in range(empty_leading)]
    row += [_cal_day(start + timedelta(days=i)) for i in range(7 - empty_leading)]
    return row


def _cal_response():
    """校历接口标准响应（V0 脱敏复刻，结构见 v0_xlxx_parsed.txt）：
    月块 0 = 表头 + 第 1/2/3 周行；月块 1 = 表头 + 第 4 周行（前置 3 个
    跨月空格）+ 第 5 周行。第 1 周行 day[0] fullText=2026-09-07（ANCHOR）。"""
    m0 = [_cal_head(),
          _cal_week(1, date(2026, 9, 7)),
          _cal_week(2, date(2026, 9, 14)),
          _cal_week(3, date(2026, 9, 21))]
    m1 = [_cal_head(),
          _cal_week(4, date(2026, 10, 1), empty_leading=3),
          _cal_week(5, date(2026, 10, 5))]
    return {"code": 0, "data": [m0, m1], "message": ""}


def test_anchor_from_calendar_full_grid():
    """N1：标准 2 月块网格 → 第 1 周行 day[0] 即锚点；表头行（head/xq 格）正确跳过。"""
    assert term.anchor_from_calendar(_cal_response()) == ANCHOR


def test_anchor_from_calendar_leading_empty_cells():
    """N2：第 1 周行前置 2 个空格 → 仍取第一个 fullText 非空格。"""
    resp = _cal_response()
    week1 = resp["data"][0][1]
    resp["data"][0][1] = [week1[0]] + [{"type": "day", "value": ""}] * 2 + week1[1:6]
    assert term.anchor_from_calendar(resp) == ANCHOR


def test_anchor_from_calendar_no_week1_row():
    """N3：全网格无 zc.value=='1' 行（第 1 周行删除）→ None。"""
    resp = _cal_response()
    resp["data"][0].pop(1)
    assert term.anchor_from_calendar(resp) is None


def test_anchor_from_calendar_non_monday():
    """N4：第 1 周行第一个非空 day 格非周一（改 2026-09-08 周二）→ None（不跳格采信后续格）。"""
    resp = _cal_response()
    resp["data"][0][1][1]["fullText"] = "2026-09-08"
    assert term.anchor_from_calendar(resp) is None


def test_anchor_from_calendar_bad_date_format():
    """N5：fullText 日期格式非法（2026/09/07）→ None。"""
    resp = _cal_response()
    resp["data"][0][1][1]["fullText"] = "2026/09/07"
    assert term.anchor_from_calendar(resp) is None


def test_anchor_from_calendar_invalid_data():
    """N6：response 非 dict / data 非 list（dict/None/str/[]）→ 一律 None。"""
    assert term.anchor_from_calendar({"code": 0, "data": {"x": 1}}) is None
    assert term.anchor_from_calendar({"code": 0, "data": None}) is None
    assert term.anchor_from_calendar({"code": 0, "data": "not-a-list"}) is None
    assert term.anchor_from_calendar({"code": 0, "data": []}) is None
    assert term.anchor_from_calendar(["not-a-dict"]) is None
    assert term.anchor_from_calendar(None) is None


def test_fetch_anchor_invalid_grid_no_write(isolated_dirs, monkeypatch, caplog):
    """N7（F5 分支）：解析 None → 返回 False + 零写入不变式 + 分级告警。"""
    client = JWXTClient(account="u", password="p")
    client.xnxqdm = "20261"
    resp = _cal_response()
    resp["data"][0].pop(1)
    monkeypatch.setattr(client, "_fetch_calendar", lambda: resp)
    with caplog.at_level(logging.WARNING, logger="lit.jwxt.client"):
        assert client.fetch_term_anchor() is False
        assert "校历直取失败" in caplog.text
    assert not state.read_state()