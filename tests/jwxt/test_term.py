"""学期域纯函数组：锚点存取/周次计算/双条件过滤/探测反推（spec 5.1-5.3 冻结行为）。"""
import json
import os
from datetime import date

from lit.jwxt import state, term


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


def test_available_probe_weeks_sorted_dedup():
    courses = [
        {"weeks": [3, 1]},
        {"weeks": [1, 2, 3]},
        {"weeks": []},
    ]
    assert term.available_probe_weeks(courses) == [1, 2, 3]


def test_available_probe_weeks_all_empty():
    assert term.available_probe_weeks([{"weeks": []}, {"weeks": []}]) == []
    assert term.available_probe_weeks([]) == []


def test_anchor_from_week_response_consistent():
    raw = [
        {"qsrq": "2026-10-19"},
        {"qsrq": "2026-10-21"},
        {"qsrq": "2026-10-23"},
    ]
    assert term.anchor_from_week_response(raw, 7) == ANCHOR


def test_anchor_from_week_response_reschedule_outlier():
    raw = [
        {"qsrq": "2026-10-19"},
        {"qsrq": "2026-10-20"},
        {"qsrq": "2026-10-22"},
        {"qsrq": "2026-10-28"},  # 调课：日期落入第 8 日历周 → 离群票
    ]
    assert term.anchor_from_week_response(raw, 7) == ANCHOR


def test_anchor_from_week_response_tie_fails():
    raw = [
        {"qsrq": "2026-10-19"},
        {"qsrq": "2026-10-26"},  # 两票各半
    ]
    assert term.anchor_from_week_response(raw, 7) is None


def test_anchor_from_week_response_no_date_field():
    assert term.anchor_from_week_response([{"kcmc": "x"}], 7) is None
    assert term.anchor_from_week_response([], 7) is None


def test_anchor_from_week_response_single_candidate():
    raw = [{"qsrq": "2026-10-19"}]
    assert term.anchor_from_week_response(raw, 7) == ANCHOR


def test_anchor_from_week_response_week1():
    raw = [{"qsrq": "2026-09-07"}, {"qsrq": "2026-09-09"}]
    assert term.anchor_from_week_response(raw, 1) == ANCHOR