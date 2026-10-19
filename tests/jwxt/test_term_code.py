"""fetch_today_context 契约测试（N1，jwxt_pure_api T 区）：
今日条目直取三合一（H1）/ 学期界（H2）/ 跨月构造（H3）/ code<0 与网络异常上抛 /
请求形态硬约束回归锚点（body month=YYYY-MM + Referer /new/desktop + X-Requested-With）。"""
from datetime import date

import pytest
import requests

from lit.jwxt.api.term_code import fetch_today_context


class StubResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, payload=None, exc=None):
        self.calls = []
        self._payload = payload
        self._exc = exc

    def post(self, url, data=None, headers=None, timeout=None):
        self.calls.append({"url": url, "data": dict(data or {}),
                           "headers": dict(headers or {}), "timeout": timeout})
        if self._exc is not None:
            raise self._exc
        return StubResponse(self._payload)


def test_today_entry_direct_hit():
    """今日条目直取：zc/xnxqdm/in_term=True 三合一（H1）+ 请求形态硬约束。"""
    payload = {"code": 0, "data": [
        {"rq": "2026-10-19", "xnxqdm": "202601", "zc": "7"},
        {"rq": "2026-10-20", "xnxqdm": "202601", "zc": "7"},
        {"rq": "2026-10-21", "xnxqdm": "202601", "zc": "7"},
    ]}
    s = FakeSession(payload)
    ctx = fetch_today_context(s, "http://jw.example", date(2026, 10, 20))
    assert ctx.xnxqdm == "202601"
    assert ctx.zc == 7
    assert ctx.in_term is True
    assert ctx.rq == date(2026, 10, 20)
    call = s.calls[0]
    assert call["url"] == "http://jw.example/new/curMonthXnxq"
    assert call["data"] == {"month": "2026-10"}
    assert call["headers"]["X-Requested-With"] == "XMLHttpRequest"
    assert call["headers"]["Referer"] == "http://jw.example/new/desktop"
    assert call["timeout"] == 15


def test_today_entry_v0_october_form():
    """V0 样本 10 月形态：整月条目中命中今日（zc 字符串 → int）。"""
    data = [{"rq": f"2026-10-{d:02d}", "xnxqdm": "202601", "zc": "8"}
            for d in range(19, 26)]
    ctx = fetch_today_context(FakeSession({"code": 0, "data": data}),
                              "http://jw.example", date(2026, 10, 22))
    assert (ctx.xnxqdm, ctx.zc, ctx.in_term) == ("202601", 8, True)


def test_out_of_term_no_today_entry():
    """data 无今日条目 → 学期外（H2：学期界天然表达，学期末条目止于 1 月中旬）。"""
    payload = {"code": 0, "data": [
        {"rq": f"2027-01-{d:02d}", "xnxqdm": "202601", "zc": "20"}
        for d in (11, 12, 13, 14, 15, 16, 17)
    ]}
    ctx = fetch_today_context(FakeSession(payload), "http://jw.example", date(2027, 1, 20))
    assert ctx.in_term is False
    assert ctx.xnxqdm == ""
    assert ctx.zc == 0


def test_out_of_term_empty_data():
    """寒假月 data=[] → 学期外（H2）。"""
    ctx = fetch_today_context(FakeSession({"code": 0, "data": []}),
                              "http://jw.example", date(2027, 2, 10))
    assert ctx.in_term is False
    assert ctx.zc == 0


def test_month_construction_cross_month():
    """跨月边界（H3 推演）：2027-03 新学期首日 → body month=2027-03，xnxqdm=202701。"""
    payload = {"code": 0, "data": [{"rq": "2027-03-01", "xnxqdm": "202701", "zc": "1"}]}
    s = FakeSession(payload)
    ctx = fetch_today_context(s, "http://jw.example", date(2027, 3, 1))
    assert (ctx.xnxqdm, ctx.zc, ctx.in_term) == ("202701", 1, True)
    assert s.calls[0]["data"] == {"month": "2027-03"}


def test_month_construction_month_precision():
    """month 仅精确到月：2026-11-30 请求 month=2026-11（零本地日期算术补救）。"""
    s = FakeSession({"code": 0, "data": []})
    fetch_today_context(s, "http://jw.example", date(2026, 11, 30))
    assert s.calls[0]["data"] == {"month": "2026-11"}


def test_code_negative_raises():
    """code<0 → ValueError（调用方按会话失效语义处理）。"""
    with pytest.raises(ValueError):
        fetch_today_context(FakeSession({"code": -1, "message": "会话无效"}),
                            "http://jw.example", date(2026, 10, 20))


def test_network_exception_propagates():
    """网络异常自然上抛（不吞不重试）。"""
    with pytest.raises(requests.ConnectionError):
        fetch_today_context(FakeSession(exc=requests.ConnectionError("network down")),
                            "http://jw.example", date(2026, 10, 20))


def test_data_invalid_structure_returns_none():
    """data 非 list → None（调用方按 F2 降级）。"""
    ctx = fetch_today_context(FakeSession({"code": 0, "data": {"rq": "2026-10-20"}}),
                              "http://jw.example", date(2026, 10, 20))
    assert ctx is None


def test_response_first_day_semantics():
    """响应首日语义（H2）：2026-09 响应首日 09-07（周一）zc=1。"""
    data = [{"rq": f"2026-09-{d:02d}", "xnxqdm": "202601", "zc": str(1 + (d - 7) // 7)}
            for d in range(7, 31)]
    ctx = fetch_today_context(FakeSession({"code": 0, "data": data}),
                              "http://jw.example", date(2026, 9, 7))
    assert ctx.zc == 1
    assert ctx.in_term is True