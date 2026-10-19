"""启动迁移组：旧平铺布局 → 按域分组布局（spec 5.1 迁移规则 + 幂等 + 容错）。"""
import json
import os

from lit import layout


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def write_bytes(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(payload)


def read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def seed_legacy_layout(d):
    """构造完整旧布局：两旧 JSON + lock + 凭证 + 课表 + tts_cache 两文件。"""
    write_json(os.path.join(d["data"], "jwxt_cookies.json"), {
        "cookies": {"JSESSIONID": "j1"},
        "login_time": "2026-10-18T10:00:00",
        "account": "u1",
    })
    write_json(os.path.join(d["data"], "jwxt_xnxqdm.json"),
               {"xnxqdm": "20261", "updated": "2026-10-18T11:00:00"})
    write_json(os.path.join(d["data"], "jwxt_lock.json"), {
        "account": "u1",
        "lock_until": "2026-10-20T15:00:00",
        "failed_attempts": 3,
    })
    write_json(os.path.join(d["data"], "xiaoai_credentials.json"), {
        "user_id": "42", "service_token": "st",
        "pass_token": "pt", "device_id": "dev",
    })
    write_json(os.path.join(d["cache"], "course_table.json"), {
        "updated": "2026-10-18T06:00:00",
        "courses": [{"name": "高数", "day": 1}],
    })
    write_bytes(os.path.join(d["data"], "tts_cache", "a.mp3"), b"\x00" * 100)
    write_bytes(os.path.join(d["data"], "tts_cache", "b.mp3"), b"\x00" * 200)


def test_migration_full_layout(isolated_dirs):
    seed_legacy_layout(isolated_dirs)
    layout.run_startup_migration()
    d = isolated_dirs
    state_data = read_json(os.path.join(d["data"], "jwxt", "jwxt.json"))
    assert state_data["cookies"] == {"JSESSIONID": "j1"}
    assert state_data["login_time"] == "2026-10-18T10:00:00"
    assert state_data["account"] == "u1"
    assert state_data["xnxqdm"] == "20261"
    assert state_data["updated"] == "2026-10-18T11:00:00"
    assert read_json(
        os.path.join(d["data"], "jwxt", "jwxt_lock.json"))["failed_attempts"] == 3
    assert read_json(
        os.path.join(d["data"], "xiaoai", "xiaoai_credentials.json"))["user_id"] == "42"
    assert read_json(
        os.path.join(d["cache"], "jwxt", "course_table.json"))["courses"][0]["name"] == "高数"
    tts_dir = os.path.join(d["cache"], "xiaoai", "tts_cache")
    assert sorted(os.listdir(tts_dir)) == ["a.mp3", "b.mp3"]
    assert os.path.getsize(os.path.join(tts_dir, "b.mp3")) == 200
    assert not os.path.exists(os.path.join(d["data"], "tts_cache"))
    for legacy in ("jwxt_cookies.json", "jwxt_xnxqdm.json",
                   "jwxt_lock.json", "xiaoai_credentials.json"):
        assert not os.path.exists(os.path.join(d["data"], legacy))
    assert not os.path.exists(os.path.join(d["cache"], "course_table.json"))


def test_migration_idempotent(isolated_dirs):
    seed_legacy_layout(isolated_dirs)
    layout.run_startup_migration()
    d = isolated_dirs
    snapshot = {}
    for root in (d["data"], d["cache"]):
        for dirpath, _, files in os.walk(root):
            for name in files:
                p = os.path.join(dirpath, name)
                snapshot[p] = os.path.getmtime(p)
    layout.run_startup_migration()
    current = {}
    for root in (d["data"], d["cache"]):
        for dirpath, _, files in os.walk(root):
            for name in files:
                p = os.path.join(dirpath, name)
                current[p] = os.path.getmtime(p)
    assert current == snapshot  # 二次调用零动作


def test_migration_skips_existing_target(isolated_dirs):
    d = isolated_dirs
    write_json(os.path.join(d["data"], "jwxt_lock.json"),
               {"account": "u1", "lock_until": None, "failed_attempts": 1})
    write_json(os.path.join(d["data"], "jwxt", "jwxt_lock.json"),
               {"account": "u2", "lock_until": None, "failed_attempts": 2})
    layout.run_startup_migration()
    assert read_json(
        os.path.join(d["data"], "jwxt", "jwxt_lock.json"))["account"] == "u2"
    assert os.path.exists(os.path.join(d["data"], "jwxt_lock.json"))


def test_migration_tts_cache_keeps_dir_when_file_remains(isolated_dirs):
    d = isolated_dirs
    write_bytes(os.path.join(d["data"], "tts_cache", "dup.mp3"), b"\x00" * 10)
    write_bytes(os.path.join(d["cache"], "xiaoai", "tts_cache", "dup.mp3"),
                b"\x00" * 20)
    layout.run_startup_migration()
    assert os.path.isdir(os.path.join(d["data"], "tts_cache"))
    assert os.path.getsize(
        os.path.join(d["data"], "tts_cache", "dup.mp3")) == 10
    assert os.path.getsize(
        os.path.join(d["cache"], "xiaoai", "tts_cache", "dup.mp3")) == 20


def test_migration_corrupt_legacy_cookies(isolated_dirs):
    d = isolated_dirs
    with open(os.path.join(d["data"], "jwxt_cookies.json"), "w",
              encoding="utf-8") as f:
        f.write("{broken")
    write_json(os.path.join(d["data"], "jwxt_xnxqdm.json"),
               {"xnxqdm": "20261", "updated": "2026-10-18T11:00:00"})
    layout.run_startup_migration()
    state_data = read_json(os.path.join(d["data"], "jwxt", "jwxt.json"))
    assert state_data["cookies"] == {}
    assert state_data["xnxqdm"] == "20261"
    assert os.path.exists(os.path.join(d["data"], "jwxt_cookies.json"))
    assert not os.path.exists(os.path.join(d["data"], "jwxt_xnxqdm.json"))


def test_migration_empty_layout_noop(isolated_dirs):
    layout.run_startup_migration()
    assert os.listdir(isolated_dirs["data"]) == []
    assert os.listdir(isolated_dirs["cache"]) == []


def test_repo_template_layout():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assert os.path.isfile(
        os.path.join(repo_root, "lit", "web", "templates", "index.html"))
    assert not os.path.exists(os.path.join(repo_root, "templates"))
    with open(os.path.join(repo_root, "lit", "web", "app.py"),
              "r", encoding="utf-8") as f:
        assert "BASE_DIR" not in f.read()