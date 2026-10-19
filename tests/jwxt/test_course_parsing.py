"""课表解析组：parse_courses 10 键映射与 qsrq/qssj 提取（jwxt_pure_api 契约，H7）。"""
from lit.jwxt.api.week_table import parse_courses


def test_parse_full_fields(isolated_dirs):
    raw = [{
        "kcmc": "形势与政策", "ps": "5", "pe": "6", "xq": "1", "qsrq": "2026-10-22",
        "qssj": "14:30:00", "jssj": "16:10:00", "jxcdmc": "XD2", "teaxms": "张三",
    }]
    courses = parse_courses(raw)
    assert len(courses) == 1
    c = courses[0]
    assert c["name"] == "形势与政策"
    assert c["section_start"] == 5
    assert c["section_end"] == 6
    assert c["day"] == 1
    assert c["date"] == "2026-10-22"
    assert c["start_time"] == "14:30:00"
    assert c["end_time"] == "16:10:00"
    assert c["classroom"] == "XD2"
    assert c["teacher"] == "张三"


def test_parse_missing_fields_defaults(isolated_dirs):
    courses = parse_courses([{}])
    c = courses[0]
    assert c["name"] == ""
    assert c["section_start"] == 0
    assert c["section_end"] == 0
    assert c["day"] == 0
    assert c["date"] == ""
    assert c["start_time"] == ""
    assert c["end_time"] == ""
    assert c["classroom"] == ""
    assert c["teacher"] == ""
    c2 = parse_courses([{"ps": "0", "pe": "0"}])[0]
    assert c2["section_start"] == 0
    assert c2["section_end"] == 0


def test_parse_multiple_items(isolated_dirs):
    raw = [{"kcmc": "A", "qsrq": "2026-10-20"},
           {"kcmc": "B", "qsrq": "2026-10-21", "ps": "3", "pe": "4"}]
    courses = parse_courses(raw)
    assert [c["name"] for c in courses] == ["A", "B"]
    assert [c["date"] for c in courses] == ["2026-10-20", "2026-10-21"]
    assert courses[1]["section_start"] == 3
    assert courses[1]["section_end"] == 4