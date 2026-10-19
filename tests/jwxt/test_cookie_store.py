"""Cookie 加载组：jwxt.json cookies 域持久化与账号匹配（spec 6.2 条 1 冻结行为）。"""
import json
import os
from datetime import datetime

from lit.jwxt import JWXTClient, cookies, xnxqdm


def write_cookies(data_dir, payload):
    jwxt_dir = os.path.join(data_dir, "jwxt")
    os.makedirs(jwxt_dir, exist_ok=True)
    path = os.path.join(jwxt_dir, "jwxt.json")
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
    jwxt_dir = os.path.join(isolated_dirs["data"], "jwxt")
    os.makedirs(jwxt_dir)
    path = os.path.join(jwxt_dir, "jwxt.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write("{not valid json")
    client = JWXTClient(account="u1", password="p1")
    assert client.cookie_valid is False


def test_save_cookies_writes_frozen_structure(isolated_dirs):
    client = JWXTClient(account="u1", password="p1")
    client.session.cookies.set("JSESSIONID", "xyz789")
    cookies._save_cookies(client)
    path = os.path.join(isolated_dirs["data"], "jwxt", "jwxt.json")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert set(data.keys()) == {"version", "cookies", "login_time", "account", "xnxqdm", "updated"}
    assert data["cookies"] == {"JSESSIONID": "xyz789"}
    assert data["account"] == "u1"
    assert client.cookie_valid is True
    assert client.login_time is not None


def test_save_then_reload_roundtrip(isolated_dirs):
    client = JWXTClient(account="u1", password="p1")
    client.session.cookies.set("JSESSIONID", "rt01")
    cookies._save_cookies(client)
    reloaded = JWXTClient(account="u1", password="p1")
    assert reloaded.cookie_valid is True
    assert reloaded.session.cookies.get("JSESSIONID") == "rt01"


def test_cookies_and_xnxqdm_domains_not_overwritten(isolated_dirs):
    client = JWXTClient(account="u1", password="p1")
    client.session.cookies.set("JSESSIONID", "rt02")
    cookies._save_cookies(client)
    client.xnxqdm = "20261"
    xnxqdm._save_xnxqdm(client)
    path = os.path.join(isolated_dirs["data"], "jwxt", "jwxt.json")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["cookies"] == {"JSESSIONID": "rt02"}
    assert data["account"] == "u1"
    assert data["xnxqdm"] == "20261"
    assert data["login_time"] is not None