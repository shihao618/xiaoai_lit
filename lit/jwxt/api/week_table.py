"""周课表 API：POST getCalendarWeekDatas（zc=N 服务端周过滤）+ 解析 + 当周缓存。

吸收原 courses.py 全部职责。请求最小形态 xnxqdm&zc=N（V0 H6：d1/d2
被完全忽略可省略）；当日判定字段 date=qsrq（H7：调课课程出现在系统
指定实际日期，无需客户端推算）。
"""
import json
import logging
import os
from datetime import date, datetime
from typing import TypedDict

import requests

from lit import config

logger = logging.getLogger(__name__)


class CourseEntry(TypedDict):
    name: str            # kcmc
    section_start: int   # ps
    section_end: int     # pe
    day: int             # xq（星期 1-7，系统字段保留）
    date: str            # qsrq（"2026-10-22" 上课日期，当日判定字段——H7）
    start_time: str      # qssj（"08:00:00"）
    end_time: str        # jssj（"09:40:00"）
    classroom: str       # jxcdmc
    teacher: str         # teaxms


def parse_courses(raw_courses) -> list[CourseEntry]:
    """45 键原始条目 → 课程模型（ps/pe/xq 容错解析，缺失默认 0）。"""
    courses = []
    for item in raw_courses:
        ps = int(item.get("ps", "0"))
        pe = int(item.get("pe", "0"))
        xq = int(item.get("xq", "0"))
        courses.append({
            "name": item.get("kcmc", ""),
            "section_start": ps,
            "section_end": pe,
            "day": xq,
            "date": item.get("qsrq", ""),
            "start_time": item.get("qssj", ""),
            "end_time": item.get("jssj", ""),
            "classroom": item.get("jxcdmc", ""),
            "teacher": item.get("teaxms", ""),
        })
    return courses


def fetch_week_courses(session: requests.Session, base_url: str,
                       xnxqdm: str, zc: int) -> list[CourseEntry]:
    """POST getCalendarWeekDatas（body 最小形态 xnxqdm=...&zc=N——H6；
    Referer week.page?xnxqdm=；X-Requested-With；timeout=15）。

    code<0 或 data 非 list 抛 ValueError；返回 parse_courses 结果
    （当周课程，可空——F5 合法状态）。
    """
    resp = session.post(
        f"{base_url}/new/student/xsgrkb/getCalendarWeekDatas",
        data={"xnxqdm": xnxqdm, "zc": str(zc)},
        headers={"X-Requested-With": "XMLHttpRequest",
                 "Referer": f"{base_url}/new/student/xsgrkb/week.page?xnxqdm={xnxqdm}"},
        timeout=15,
    )
    result = resp.json()
    if result.get("code", -1) < 0:
        raise ValueError(f"周课表查询失败: {result.get('message')}")
    data = result.get("data")
    if not isinstance(data, list):
        raise ValueError("周课表响应 data 结构异常")
    return parse_courses(data)


def _cache_file() -> str:
    return os.path.join(config.CACHE_DIR, "jwxt", "course_table.json")


def save_cache(xnxqdm: str, zc: int, week_start: date, week_end: date,
               courses: list[CourseEntry]) -> None:
    """五元组缓存原子写（C10：新格式直接写，无旧格式判定分支）。

    week_start/week_end 为该教学周周一~周日（H4 周一口径），由 client
    以今日日期推定（日历算术，非教务数据推测）。缓存写入 OSError →
    warning 不阻断（缓存失败不影响主链路返回）。
    """
    path = _cache_file()
    payload = {
        "xnxqdm": xnxqdm,
        "zc": zc,
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "updated": datetime.now().isoformat(),
        "courses": courses,
    }
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError as e:
        logger.warning("课表缓存写入失败（不影响主流程）: %s", e)


def load_cache() -> dict | None:
    """读缓存：文件缺失/损坏/JSON 非法/五元组键缺失 → None
    （新格式自身结构防御校验，C10 后无旧格式判定分支）。"""
    path = _cache_file()
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    required = ("xnxqdm", "zc", "week_start", "week_end", "courses")
    if any(k not in data for k in required):
        return None
    return data


def load_cache_for_today(today: date, *, xnxqdm: str | None = None,
                         zc: int | None = None) -> list[CourseEntry] | None:
    """缓存回退入口（F4/F9）：元数据双重校验——①week_start ≤ today ≤
    week_end（日期窗口覆盖，curMonthXnxq 不可用时仍可判定）；②xnxqdm/zc
    提供时精确比对（与内存态今日上下文一致才采信）。校验不过 → None
    （F10 弃用，Q10：不拿上周当本周）。"""
    data = load_cache()
    if data is None:
        return None
    try:
        ws = date.fromisoformat(data["week_start"])
        we = date.fromisoformat(data["week_end"])
    except (TypeError, ValueError):
        return None
    if not (ws <= today <= we):
        return None
    if xnxqdm is not None and data.get("xnxqdm") != xnxqdm:
        return None
    if zc is not None and data.get("zc") != zc:
        return None
    return data.get("courses")