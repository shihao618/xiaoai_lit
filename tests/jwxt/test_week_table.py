"""周课表 API 契约测试（N2）：fetch_week_courses 请求最小形态（H6）/ code<0 /
空列表合法（F5）/ parse_courses 键提取 + 44 键样本回归 / 五元组缓存读写与防御校验。"""
import json
import os
from datetime import date
from pathlib import Path

import pytest

from lit.jwxt.api.week_table import (
    _cache_file, fetch_week_courses, load_cache, load_cache_for_today,
    parse_courses, save_cache,
)

_V0_SAMPLE = Path(__file__).resolve().parent / "fixtures" / "v0_raw_week_zc7_sample.json"


class StubResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, payload):
        self.calls = []
        self._payload = payload

    def post(self, url, data=None, headers=None, timeout=None):
        self.calls.append({"url": url, "data": dict(data or {}),
                           "headers": dict(headers or {}), "timeout": timeout})
        return StubResponse(self._payload)


def _v0_sample() -> dict:
    """真实接口 44 键样本回归（fixture 化，随 tests/ 入库）：防字段名笔误；
    teaxms/pkr/teadms/pkrdms 已脱敏（V0 报告惯例）。"""
    with open(_V0_SAMPLE, "r", encoding="utf-8") as f:
        return json.load(f)


COURSES = [
    {"name": "高数", "section_start": 1, "section_end": 2, "day": 2,
     "date": "2026-10-20", "start_time": "08:00:00", "end_time": "09:40:00",
     "classroom": "A101", "teacher": "教师A"},
]


def test_fetch_week_courses_minimal_request_body():
    """请求体最小形态（H6）：xnxqdm&zc=N，无 d1/d2/szxqdm/yxdm。"""
    s = FakeSession({"code": 0, "data": []})
    fetch_week_courses(s, "http://jw.example", "202601", 7)
    call = s.calls[0]
    assert call["url"] == "http://jw.example/new/student/xsgrkb/getCalendarWeekDatas"
    assert call["data"] == {"xnxqdm": "202601", "zc": "7"}
    assert set(call["data"]) == {"xnxqdm", "zc"}
    assert call["headers"]["X-Requested-With"] == "XMLHttpRequest"
    assert "week.page?xnxqdm=202601" in call["headers"]["Referer"]
    assert call["timeout"] == 15


def test_fetch_week_courses_code_negative_raises():
    with pytest.raises(ValueError):
        fetch_week_courses(FakeSession({"code": -1, "message": "会话无效"}),
                           "http://jw.example", "202601", 7)


def test_fetch_week_courses_empty_list_legal():
    """空列表合法（F5：当周无课为正常状态）。"""
    assert fetch_week_courses(FakeSession({"code": 0, "data": []}),
                              "http://jw.example", "202601", 7) == []


def test_parse_courses_date_start_time_extraction():
    courses = parse_courses([{"kcmc": "高数", "ps": "01", "pe": "02", "xq": "3",
                              "qsrq": "2026-10-21", "qssj": "10:10:00",
                              "jssj": "11:50:00", "jxcdmc": "A202", "teaxms": "教师B"}])
    c = courses[0]
    assert c["date"] == "2026-10-21"       # qsrq → date（H7 当日判定字段）
    assert c["start_time"] == "10:10:00"   # qssj
    assert c["end_time"] == "11:50:00"
    assert c["name"] == "高数"
    assert c["section_start"] == 1
    assert c["section_end"] == 2
    assert c["day"] == 3
    assert c["classroom"] == "A202"
    assert c["teacher"] == "教师B"


def test_parse_courses_v0_44key_sample():
    """V0 真实样本解析（v0_raw_week_zc7.json sample_first，已脱敏为 44 键 fixture）。
    实测 44 键（spec 引用 V0 报告"45 键"口径，JSON 实测 44——Q9 如实固化）。"""
    sample = _v0_sample()
    assert len(sample) == 44
    c = parse_courses([sample])[0]
    assert c == {
        "name": "复变函数与积分变换III",
        "section_start": 1,
        "section_end": 2,
        "day": 4,
        "date": "2026-10-22",
        "start_time": "08:00:00",
        "end_time": "09:40:00",
        "classroom": "XC3",
        "teacher": "教师A",
    }


def test_save_cache_five_tuple_persistence(isolated_dirs):
    """五元组落盘：xnxqdm/zc/week_start/week_end/courses + 原子写零 tmp 残留。"""
    save_cache("202601", 7, date(2026, 10, 19), date(2026, 10, 25), COURSES)
    data = load_cache()
    assert data["xnxqdm"] == "202601"
    assert data["zc"] == 7
    assert data["week_start"] == "2026-10-19"
    assert data["week_end"] == "2026-10-25"
    assert data["courses"] == COURSES
    assert "updated" in data
    assert not os.path.exists(_cache_file() + ".tmp")


def test_load_cache_for_today_window_covered(isolated_dirs):
    """窗口覆盖今日 → 返回缓存课程。"""
    save_cache("202601", 7, date(2026, 10, 19), date(2026, 10, 25), COURSES)
    assert load_cache_for_today(date(2026, 10, 21)) == COURSES


def test_load_cache_for_today_window_not_covered(isolated_dirs):
    """窗口不覆盖今日（上周/下周缓存）→ None。"""
    save_cache("202601", 7, date(2026, 10, 19), date(2026, 10, 25), COURSES)
    assert load_cache_for_today(date(2026, 10, 18)) is None
    assert load_cache_for_today(date(2026, 10, 26)) is None


def test_load_cache_for_today_metadata_mismatch_discarded(isolated_dirs):
    """xnxqdm/zc 不一致 → 弃用（Q10 方向：不拿上周当本周）。"""
    save_cache("202601", 7, date(2026, 10, 19), date(2026, 10, 25), COURSES)
    assert load_cache_for_today(date(2026, 10, 21), xnxqdm="202601", zc=7) == COURSES
    assert load_cache_for_today(date(2026, 10, 21), xnxqdm="202601", zc=8) is None
    assert load_cache_for_today(date(2026, 10, 21), xnxqdm="202701", zc=7) is None


def test_load_cache_invalid_json_returns_none(isolated_dirs):
    """JSON 非法 → None（新格式自身防御，C10 后无旧格式判定分支）。"""
    path = _cache_file()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("{not-valid-json")
    assert load_cache() is None
    assert load_cache_for_today(date(2026, 10, 21)) is None


def test_load_cache_missing_five_tuple_keys_returns_none(isolated_dirs):
    """五元组键缺失 → None。"""
    path = _cache_file()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"xnxqdm": "202601", "zc": 7}, f)
    assert load_cache() is None
