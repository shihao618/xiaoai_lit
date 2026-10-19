"""教务合并状态组：jwxt.json 五键结构与读-合并-写协议（jwxt_pure_api 数据契约）。"""
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


def test_update_state_writes_full_five_keys(isolated_dirs):
    state.update_state({"xnxqdm": "20261", "updated": "2026-10-19T08:00:00"})
    data = read_json(state.state_path())
    assert set(data.keys()) == {
        "cookies", "login_time", "account", "xnxqdm", "updated"}
    assert data["cookies"] == {}
    assert data["login_time"] is None
    assert data["account"] == ""
    assert data["xnxqdm"] == "20261"
    assert data["updated"] == "2026-10-19T08:00:00"


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