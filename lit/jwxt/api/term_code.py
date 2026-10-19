"""学期代码 + 周次 + 学期界 API（POST /new/curMonthXnxq，V0 H1-H3 三合一）。

今日条目直取 zc/xnxqdm；今日不在响应 ⟺ 学期外（学期界天然表达）；
跨月/跨学期行为以 V0 H3 实测为准，禁止本地日期算术补救（spec 5.2.1 规则 2）。
"""
from dataclasses import dataclass
from datetime import date

import requests


@dataclass(frozen=True)
class TodayContext:
    xnxqdm: str      # 学年学期代码（今日所属学期）
    zc: int          # 今日教学周次
    rq: date         # 查询基准日（= 入参 today）
    in_term: bool    # 今日是否在学期内（False = 学期外/假期）


def fetch_today_context(session: requests.Session, base_url: str, today: date) -> TodayContext | None:
    """POST /new/curMonthXnxq（body: month=YYYY-MM；headers: X-Requested-With
    + Referer=/new/desktop——请求级 headers，勿污染会话头；timeout=15）。

    code<0 抛 ValueError，网络/JSON 异常自然上抛。data 中 rq==today 的
    条目直取 xnxqdm/zc（H1 三合一）；无今日条目/响应空 → in_term=False
    （H2 学期界）。返回 None 仅当 data 结构非法且无法判定学期界
    （调用方按 F2 降级）。纯查询零副作用（xnxqdm 域落盘归 client 编排）。
    """
    resp = session.post(
        f"{base_url}/new/curMonthXnxq",
        data={"month": today.strftime("%Y-%m")},
        headers={"X-Requested-With": "XMLHttpRequest",
                 "Referer": f"{base_url}/new/desktop"},
        timeout=15,
    )
    result = resp.json()
    if result.get("code", -1) < 0:
        raise ValueError(f"学期代码查询失败: {result.get('message')}")
    data = result.get("data")
    if not isinstance(data, list):
        return None
    today_iso = today.isoformat()
    for entry in data:
        if isinstance(entry, dict) and entry.get("rq") == today_iso:
            try:
                zc = int(entry.get("zc", 0))
            except (TypeError, ValueError):
                zc = 0
            return TodayContext(
                xnxqdm=str(entry.get("xnxqdm", "")),
                zc=zc,
                rq=today,
                in_term=True,
            )
    return TodayContext(xnxqdm="", zc=0, rq=today, in_term=False)