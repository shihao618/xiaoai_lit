"""课表解析组：_parse_courses 10 字段映射与 zc 解析边界（spec 5.2.1 规则 3）。"""
from jwxt import JWXTClient


def make_client(isolated_dirs):
    return JWXTClient(account="u1", password="p1")


def test_parse_full_fields(isolated_dirs):
    client = make_client(isolated_dirs)
    raw = [{
        "kcmc": "形势与政策", "ps": "5", "pe": "6", "xq": "1", "zc": "1,2,3",
        "qssj": "14:30:00", "jssj": "16:10:00", "jxcdmc": "XD2", "teaxms": "张三",
    }]
    courses = client._parse_courses(raw)
    assert len(courses) == 1
    c = courses[0]
    assert c["name"] == "形势与政策"
    assert c["section_start"] == 5
    assert c["section_end"] == 6
    assert c["day"] == 1
    assert c["start_time"] == "14:30:00"
    assert c["end_time"] == "16:10:00"
    assert c["classroom"] == "XD2"
    assert c["teacher"] == "张三"
    assert c["weeks"] == [1, 2, 3]
    assert c["week_str"] == "1,2,3"


def test_parse_missing_fields_defaults(isolated_dirs):
    client = make_client(isolated_dirs)
    courses = client._parse_courses([{}])
    c = courses[0]
    assert c["name"] == ""
    assert c["section_start"] == 0
    assert c["section_end"] == 0
    assert c["day"] == 0
    assert c["start_time"] == ""
    assert c["end_time"] == ""
    assert c["classroom"] == ""
    assert c["teacher"] == ""
    assert c["weeks"] == []
    assert c["week_str"] == ""


def test_parse_zc_plain_list(isolated_dirs):
    client = make_client(isolated_dirs)
    assert client._parse_courses([{"zc": "1,2,3"}])[0]["weeks"] == [1, 2, 3]


def test_parse_zc_spaces_stripped(isolated_dirs):
    client = make_client(isolated_dirs)
    assert client._parse_courses([{"zc": "1, 2, 3"}])[0]["weeks"] == [1, 2, 3]



def test_parse_zc_non_digit(isolated_dirs):
    client = make_client(isolated_dirs)
    assert client._parse_courses([{"zc": "1-2,1,abc"}])[0]["weeks"] == [1]


def test_parse_zc_empty(isolated_dirs):
    client = make_client(isolated_dirs)
    assert client._parse_courses([{"zc": ""}])[0]["weeks"] == []


def test_parse_ps_pe_default_string_zero(isolated_dirs):
    client = make_client(isolated_dirs)
    c = client._parse_courses([{"ps": "0", "pe": "0"}])[0]
    assert c["section_start"] == 0
    assert c["section_end"] == 0


def test_parse_multiple_items(isolated_dirs):
    client = make_client(isolated_dirs)
    raw = [{"kcmc": "A", "zc": "1"}, {"kcmc": "B", "zc": "2"}]
    courses = client._parse_courses(raw)
    assert [c["name"] for c in courses] == ["A", "B"]
    assert [c["weeks"] for c in courses] == [[1], [2]]


def test_parse_week_str_kept_verbatim(isolated_dirs):
    client = make_client(isolated_dirs)
    c = client._parse_courses([{"zc": "1,2,x"}])[0]
    assert c["weeks"] == [1, 2]
    assert c["week_str"] == "1,2,x"