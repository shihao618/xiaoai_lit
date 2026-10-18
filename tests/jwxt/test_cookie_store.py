"""Cookie 加载组：jwxt_cookies.json 持久化与账号匹配（spec 6.2 条 1 冻结行为）。"""
import json
import os
from datetime import datetime

from jwxt import JWXTClient


def write_cookies(data_dir, payload):
    path = os.path.join(data_dir, "jwxt_cookies.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    return path


def test_load_matching_account_restores(isolated_dirs):
    login_time = "2026-10-18T10:00:00"
    write_cookies(isolated_dirs["data"], {
        "cookies": {"JSESSIONID": "abc123"},
        "login_time": login_time,
        "account": "u1",
    })
    client = JWXTClient(account="u1", password="p1")
    assert client.cookie_valid is True
    assert client.logged_in is False
    assert client.session.cookies.get("JSESSIONID") == "abc123"
    assert client.login_time == datetime.fromisoformat(login_time)


def test_load_mismatched_account_skips(isolated_dirs):
    write_cookies(isolated_dirs["data"], {
        "cookies": {"JSESSIONID": "abc123"},
        "login_time": "2026-10-18T10:00:00",
        "account": "someone-else",
    })
    client = JWXTClient(account="u1", password="p1")
    assert client.cookie_valid is False
    assert client.session.cookies.get("JSESSIONID") is None


def test_load_missing_file_fresh_state(isolated_dirs):
    client = JWXTClient(account="u1", password="p1")
    assert client.cookie_valid is False
    assert client.logged_in is False
    assert client.login_time is None


def test_load_corrupt_json_warns_not_raises(isolated_dirs):
    path = os.path.join(isolated_dirs["data"], "jwxt_cookies.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write("{not valid json")
    client = JWXTClient(account="u1", password="p1")
    assert client.cookie_valid is False


def test_save_cookies_writes_frozen_structure(isolated_dirs):
    client = JWXTClient(account="u1", password="p1")
    client.session.cookies.set("JSESSIONID", "xyz789")
    client._save_cookies()
    path = os.path.join(isolated_dirs["data"], "jwxt_cookies.json")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert set(data.keys()) == {"cookies", "login_time", "account"}
    assert data["cookies"] == {"JSESSIONID": "xyz789"}
    assert data["account"] == "u1"
    assert client.cookie_valid is True
    assert client.login_time is not None


def test_save_then_reload_roundtrip(isolated_dirs):
    client = JWXTClient(account="u1", password="p1")
    client.session.cookies.set("JSESSIONID", "rt01")
    client._save_cookies()
    reloaded = JWXTClient(account="u1", password="p1")
    assert reloaded.cookie_valid is True
    assert reloaded.session.cookies.get("JSESSIONID") == "rt01"