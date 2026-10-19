"""教务合并状态组：jwxt.json 九键结构与读-合并-写协议（spec 6.2 条 2 冻结行为）。"""
import json
import os

from lit.jwxt import state


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def test_state_path_anchors_domain_subdir(isolated_dirs):
    assert state.state_path() == os.path.join(
        isolated_dirs["data"], "jwxt", "jwxt.json")


def test_read_state_missing_returns_empty(isolated_dirs):
    assert state.read_state() == {}


def test_read_state_corrupt_returns_empty(isolated_dirs):
    write_json(state.state_path(), ["not", "a", "dict"])
    assert state.read_state() == {}
    with open(state.state_path(), "w", encoding="utf-8") as f:
        f.write("{broken")
    assert state.read_state() == {}


def test_update_state_writes_full_nine_keys(isolated_dirs):
    state.update_state({"xnxqdm": "20261", "updated": "2026-10-19T08:00:00"})
    data = read_json(state.state_path())
    assert set(data.keys()) == {
        "version", "cookies", "login_time", "account", "xnxqdm", "updated",
        "term_start_monday", "term_xnxqdm", "term_start_source"}
    assert data["version"] == 1
    assert data["cookies"] == {}
    assert data["login_time"] is None
    assert data["account"] == ""
    assert data["term_start_monday"] is None
    assert data["term_xnxqdm"] == ""
    assert data["term_start_source"] == ""


def test_update_state_merge_preserves_other_domain(isolated_dirs):
    state.update_state({
        "cookies": {"JSESSIONID": "a"},
        "login_time": "2026-10-19T10:00:00",
        "account": "u1",
    })
    state.update_state({"xnxqdm": "20261", "updated": "2026-10-19T10:01:00"})
    data = read_json(state.state_path())
    assert data["cookies"] == {"JSESSIONID": "a"}
    assert data["account"] == "u1"
    assert data["login_time"] == "2026-10-19T10:00:00"
    assert data["xnxqdm"] == "20261"
    assert data["updated"] == "2026-10-19T10:01:00"


def test_update_state_atomic_no_tmp_residue(isolated_dirs):
    state.update_state({"account": "u1"})
    assert not os.path.exists(state.state_path() + ".tmp")


def test_merge_legacy_state_both_files(isolated_dirs):
    data_dir = isolated_dirs["data"]
    write_json(os.path.join(data_dir, "jwxt_cookies.json"), {
        "cookies": {"JSESSIONID": "z"},
        "login_time": "2026-10-18T10:00:00",
        "account": "u1",
    })
    write_json(os.path.join(data_dir, "jwxt_xnxqdm.json"), {
        "xnxqdm": "20261",
        "updated": "2026-10-18T11:00:00",
    })
    assert state.merge_legacy_state() is True
    data = read_json(state.state_path())
    assert data["cookies"] == {"JSESSIONID": "z"}
    assert data["login_time"] == "2026-10-18T10:00:00"
    assert data["account"] == "u1"
    assert data["xnxqdm"] == "20261"
    assert data["updated"] == "2026-10-18T11:00:00"
    assert data["version"] == 1
    assert not os.path.exists(os.path.join(data_dir, "jwxt_cookies.json"))
    assert not os.path.exists(os.path.join(data_dir, "jwxt_xnxqdm.json"))


def test_merge_legacy_state_ignores_when_new_exists(isolated_dirs):
    data_dir = isolated_dirs["data"]
    write_json(os.path.join(data_dir, "jwxt_cookies.json"),
               {"cookies": {"old": "1"}, "account": "u1"})
    write_json(state.state_path(),
               {"cookies": {"new": "2"}, "account": "u2", "version": 1})
    assert state.merge_legacy_state() is False
    data = read_json(state.state_path())
    assert data["cookies"] == {"new": "2"}
    assert os.path.exists(os.path.join(data_dir, "jwxt_cookies.json"))


def test_merge_legacy_state_corrupt_cookies_side(isolated_dirs):
    data_dir = isolated_dirs["data"]
    with open(os.path.join(data_dir, "jwxt_cookies.json"), "w",
              encoding="utf-8") as f:
        f.write("{broken")
    write_json(os.path.join(data_dir, "jwxt_xnxqdm.json"),
               {"xnxqdm": "20261", "updated": "2026-10-18T11:00:00"})
    assert state.merge_legacy_state() is True
    data = read_json(state.state_path())
    assert data["cookies"] == {}
    assert data["account"] == ""
    assert data["xnxqdm"] == "20261"
    assert os.path.exists(os.path.join(data_dir, "jwxt_cookies.json"))
    assert not os.path.exists(os.path.join(data_dir, "jwxt_xnxqdm.json"))


def test_merge_legacy_state_no_legacy_returns_false(isolated_dirs):
    assert state.merge_legacy_state() is False
    assert not os.path.exists(state.state_path())