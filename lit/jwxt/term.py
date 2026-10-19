"""学期域纯函数模块：锚点解析/有效性/持久化、教学周计算、双条件过滤、探测反推算法。

依赖方向：lit.jwxt.state + 标准库（datetime/collections）——零网络零全局状态，
不反向依赖 client（design 2.1.2 归属决策）。
"""
import logging
from collections import Counter
from datetime import date, timedelta

from lit.jwxt import state

logger = logging.getLogger(__name__)

PROBE_SAMPLE_SIZE = 3


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


def available_probe_weeks(course_list: list[dict]) -> list[int]:
    """收集课表全部课程 weeks 的并集，升序去重返回。
    空课表 / 全部 weeks 为空 → []（探测前置失败）。"""
    weeks = set()
    for c in course_list:
        weeks.update(c.get("weeks") or [])
    return sorted(weeks)


def _vote_anchor(raw_courses: list[dict], week_n: int) -> Counter:
    """逐条投票反推锚点候选（anchor_from_week_response 的内部实现，
    亦供探测编排统计票数）。"""
    votes = Counter()
    for item in raw_courses:
        raw_date = item.get("qsrq")
        if not raw_date:
            continue
        try:
            d = date.fromisoformat(raw_date)
        except (ValueError, TypeError):
            continue
        a = d - timedelta(days=d.weekday() + 7 * (week_n - 1))
        if a.weekday() != 0:
            continue
        votes[a] += 1
    return votes


def anchor_from_week_response(raw_courses: list[dict], week_n: int) -> date | None:
    """从指定周次查询响应反推学期锚点（纯数据变换，零网络）：
    逐条解析上课日期（qsrq）→ a = d − d.weekday() 天 − 7×(week_n−1) 天 →
    周一防御校验 → Counter 投票 → 唯一众数（或单候选）返回；
    平票 / 无有效票 / 无日期字段 → None。"""
    votes = _vote_anchor(raw_courses, week_n)
    if not votes:
        return None
    if len(votes) == 1:
        return next(iter(votes))
    top = votes.most_common(2)
    if top[0][1] > top[1][1]:
        return top[0][0]
    return None