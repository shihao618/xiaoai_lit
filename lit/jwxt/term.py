"""学期域纯函数模块：锚点解析/有效性/持久化、教学周计算、双条件过滤、校历网格锚点解析。

依赖方向：lit.jwxt.state + 标准库（datetime）——零网络零全局状态，
不反向依赖 client（design 2.1.2 归属决策）。
"""
import logging
from datetime import date

from lit.jwxt import state

logger = logging.getLogger(__name__)


def parse_anchor(raw: str) -> date | None:
    """解析锚点日期串并校验周一：合法 ISO(YYYY-MM-DD) 周一 → date；
    空/格式非法/非周一 → None。读取侧与配置侧共用的双侧校验原语。"""
    if not raw:
        return None
    try:
        d = date.fromisoformat(raw)
    except (ValueError, TypeError):
        return None
    if d.weekday() != 0:
        return None
    return d


def get_effective_anchor(current_xnxqdm: str) -> date | None:
    """读取 jwxt.json 锚点域并做三重校验：
    ① parse_anchor（格式 + 周一）② term_xnxqdm 与 current_xnxqdm 一致
    （不一致视为学期切换失效）。任一不过 → None。纯读无副作用。"""
    data = state.read_state()
    anchor = parse_anchor(data.get("term_start_monday"))
    if anchor is None:
        return None
    if data.get("term_xnxqdm", "") != current_xnxqdm:
        logger.warning(
            "学期锚点与当前学期不一致（%s ≠ %s），视为学期切换失效",
            data.get("term_xnxqdm"), current_xnxqdm)
        return None
    return anchor


def save_anchor(anchor: date, xnxqdm: str, source: str) -> None:
    """锚点三键成对原子写入：term_start_monday / term_xnxqdm / term_start_source。
    经 state.update_state 读-合并-写——cookies/xnxqdm 既有域零触碰。"""
    state.update_state({
        "term_start_monday": anchor.isoformat(),
        "term_xnxqdm": xnxqdm,
        "term_start_source": source,
    })


def current_week(anchor: date, today: date) -> int:
    """由学期锚点（第 1 教学周周一）推算当前教学周：
    (today - anchor).days // 7 + 1。学期外返回 ≤ 0（由调用方分支处理）。"""
    return (today - anchor).days // 7 + 1


def filter_today(course_list: list[dict], day: int, week: int) -> list[dict]:
    """双条件过滤：保留 day 匹配且（weeks 为空 或 week ∈ weeks）的课程。
    weeks 为空视为每周上课（Q2）。返回新列表，不改入参；排序由调用方执行。"""
    return [
        c for c in course_list
        if c.get("day") == day and (not (c.get("weeks") or []) or week in (c.get("weeks") or []))
    ]


def anchor_from_calendar(response: dict) -> date | None:
    """从校历接口响应网格解析第 1 教学周周一（纯数据变换，零网络）：
    data 为月块数组，每块含周行（首格 type=='zc'），第 1 周行内第一个
    fullText 非空 day 格即学期锚点（跨月空格无 fullText 天然跳过）。
    非周一不跳格直接判失败（网格结构已变，不写半吊子锚点）；
    输入结构非法 / 无第 1 周行 / 日期格式非法 → 一律 None。"""
    if not isinstance(response, dict):
        return None
    data = response.get("data")
    if not isinstance(data, list):
        return None
    for month in data:
        if not isinstance(month, list):
            continue
        for row in month:
            if not isinstance(row, list):
                continue
            is_week1 = any(
                isinstance(c, dict) and c.get("type") == "zc" and str(c.get("value", "")) == "1"
                for c in row
            )
            if not is_week1:
                continue
            for c in row:
                if not isinstance(c, dict) or c.get("type") != "day":
                    continue
                full = c.get("fullText")
                if not isinstance(full, str) or not full:
                    continue
                try:
                    d = date.fromisoformat(full)
                except (ValueError, TypeError):
                    return None
                if d.weekday() != 0:
                    return None
                return d
            return None
    return None