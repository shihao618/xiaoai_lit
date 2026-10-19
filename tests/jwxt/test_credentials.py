"""凭证持久化模块测试（N3）：三键 0o600 原子写 / 损坏防御（F11）/
resolve 优先级（F12/F13）/ 密码零回显审计（硬约束 6 测试锚点）。"""
import json
import logging
import os
import stat

from lit.jwxt import credentials as cred
from lit.jwxt.client import JWXTClient

NINE_KEYS = {
    "logged_in", "cookie_valid", "login_time", "account", "locked",
    "lock_time", "failed_attempts", "xnxqdm", "current_week",
}


def _credentials_file(isolated_dirs):
    return os.path.join(isolated_dirs["data"], "jwxt", "credentials.json")


def test_save_credentials_three_keys_atomic(isolated_dirs):
    """三键 {account, password, updated} 原子写 + tmp 残留零。"""
    cred.save_credentials("u1", "pw1")
    path = _credentials_file(isolated_dirs)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert set(data.keys()) == {"account", "password", "updated"}
    assert data["account"] == "u1"
    assert data["password"] == "pw1"
    assert data["updated"]
    assert not os.path.exists(path + ".tmp")


def test_save_credentials_overwrite_and_permission(isolated_dirs):
    """二次保存覆盖旧值 + 0o600（Windows 宿主 chmod 近似 no-op，design 2.5.4）。"""
    cred.save_credentials("u1", "pw1")
    cred.save_credentials("u2", "pw2")
    assert cred.load_credentials() == ("u2", "pw2")
    mode = stat.S_IMODE(os.stat(_credentials_file(isolated_dirs)).st_mode)
    if os.name == "posix":
        assert mode == 0o600
    else:
        assert mode & stat.S_IWRITE


def test_load_credentials_normal(isolated_dirs):
    cred.save_credentials("u1", "pw1")
    assert cred.load_credentials() == ("u1", "pw1")


def test_load_credentials_missing_returns_none(isolated_dirs):
    assert cred.load_credentials() is None


def test_load_credentials_corrupt_returns_none(isolated_dirs):
    """JSON 非法 → None（F11：视为无持久化凭证）。"""
    path = _credentials_file(isolated_dirs)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("{broken")
    assert cred.load_credentials() is None


def test_load_credentials_invalid_shape_returns_none(isolated_dirs):
    """顶层非对象 / 空账号密码 / 键缺失 → None。"""
    path = _credentials_file(isolated_dirs)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(["not", "a", "dict"], f)
    assert cred.load_credentials() is None
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"account": "", "password": ""}, f)
    assert cred.load_credentials() is None
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"account": "u1"}, f)
    assert cred.load_credentials() is None


def test_resolve_credentials_persisted_wins(isolated_dirs):
    """持久化优先（F13：宿主改环境变量不生效）。"""
    cred.save_credentials("persisted", "persisted-pw")
    assert cred.resolve_credentials("env", "env-pw") == ("persisted", "persisted-pw")


def test_resolve_credentials_env_fallback(isolated_dirs):
    """持久化缺失 → 环境变量回退（F12 引导态）。"""
    assert cred.resolve_credentials("env", "env-pw") == ("env", "env-pw")


def test_resolve_credentials_none_when_absent(isolated_dirs):
    """双无 / 单侧缺失 → None。"""
    assert cred.resolve_credentials("", "") is None
    assert cred.resolve_credentials("only-account", "") is None


def test_password_zero_echo_audit(isolated_dirs, caplog):
    """密码零回显审计（硬约束 6）：get_status 9 键无 password 键 +
    caplog 全级别扫描无密码值。"""
    client = JWXTClient(account="u1", password="s3cret-audit-pw")
    with caplog.at_level(logging.DEBUG):
        cred.save_credentials(client.account, client.password)
        status = client.get_status()
    assert set(status.keys()) == NINE_KEYS
    assert "password" not in status
    rendered = "\n".join(r.getMessage() for r in caplog.records)
    assert "s3cret-audit-pw" not in rendered