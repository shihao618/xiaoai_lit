import json
from datetime import datetime

from lit.xiaoai.credentials import CredentialStore, Credentials


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_save_load_roundtrip(tmp_path):
    store = CredentialStore(data_dir=str(tmp_path))
    cred = Credentials(
        user_id="123", pass_token="p", device_id="d",
        service_token="s", security="sec",
        refreshed_at=datetime(2026, 1, 1, 12, 0),
        default_device_id="dev1",
    )
    assert cred.is_usable()
    store.save(cred)
    loaded = store.load()
    assert loaded is not None
    assert loaded.user_id == "123"
    assert loaded.pass_token == "p"
    assert loaded.device_id == "d"
    assert loaded.service_token == "s"
    assert loaded.security == "sec"
    assert loaded.default_device_id == "dev1"
    assert loaded.refreshed_at == datetime(2026, 1, 1, 12, 0)
    assert loaded.rejected is False


def test_is_usable_false_cases():
    assert not Credentials().is_usable()
    assert not Credentials(user_id="1", service_token="s", rejected=True).is_usable()
    assert not Credentials(user_id="1").is_usable()
    assert not Credentials(service_token="s").is_usable()


def test_load_new_format_rejected_flag(tmp_path):
    write(tmp_path / "xiaoai_credentials.json",
          {"user_id": "1", "service_token": "s", "rejected": True})
    cred = CredentialStore(data_dir=str(tmp_path)).load()
    assert cred.rejected is True
    assert cred.is_usable() is False


def test_load_mi_token(tmp_path):
    write(tmp_path / "mi.token", {
        "userId": "42", "deviceId": "dev", "passToken": "pt",
        "serviceToken": "st", "ssecurity": "ss",
    })
    cred = CredentialStore(data_dir=str(tmp_path)).load()
    assert cred.user_id == "42"
    assert cred.device_id == "dev"
    assert cred.pass_token == "pt"
    assert cred.service_token == "st"
    assert cred.security == "ss"


def test_load_manual_cookies_degraded(tmp_path):
    write(tmp_path / "xiaoai_cookies.json", {
        "cookies": {"userId": "7", "serviceToken": "tok"},
        "username": "u", "login_time": "2026-01-01",
    })
    cred = CredentialStore(data_dir=str(tmp_path)).load()
    assert cred.user_id == "7"
    assert cred.service_token == "tok"
    assert cred.pass_token == ""
    assert cred.is_usable()


def test_priority_new_format_over_mi_token(tmp_path):
    write(tmp_path / "xiaoai_credentials.json",
          {"user_id": "new", "service_token": "s1"})
    write(tmp_path / "mi.token", {"userId": "old", "serviceToken": "s2"})
    cred = CredentialStore(data_dir=str(tmp_path)).load()
    assert cred.user_id == "new"


def test_priority_mi_token_over_cookies(tmp_path):
    write(tmp_path / "mi.token", {"userId": "42", "serviceToken": "st"})
    write(tmp_path / "xiaoai_cookies.json",
          {"cookies": {"userId": "7", "serviceToken": "tok"}})
    cred = CredentialStore(data_dir=str(tmp_path)).load()
    assert cred.user_id == "42"


def test_corrupt_new_format_skipped(tmp_path):
    (tmp_path / "xiaoai_credentials.json").write_text("{broken", encoding="utf-8")
    write(tmp_path / "mi.token", {"userId": "42", "serviceToken": "st"})
    cred = CredentialStore(data_dir=str(tmp_path)).load()
    assert cred.user_id == "42"


def test_corrupt_all_formats_returns_none(tmp_path):
    (tmp_path / "xiaoai_credentials.json").write_text("{broken", encoding="utf-8")
    (tmp_path / "mi.token").write_text("not json", encoding="utf-8")
    (tmp_path / "xiaoai_cookies.json").write_text("[]", encoding="utf-8")
    assert CredentialStore(data_dir=str(tmp_path)).load() is None


def test_all_missing_returns_none(tmp_path):
    assert CredentialStore(data_dir=str(tmp_path)).load() is None


def test_legacy_default_device_migration(tmp_path):
    write(tmp_path / "mi.token", {"userId": "42", "serviceToken": "st"})
    write(tmp_path / "xiaoai_token.json",
          {"username": "u", "device_id": "speaker-1", "login_time": None})
    cred = CredentialStore(data_dir=str(tmp_path)).load()
    assert cred.default_device_id == "speaker-1"


def test_mark_rejected(tmp_path):
    store = CredentialStore(data_dir=str(tmp_path))
    store.save(Credentials(user_id="1", pass_token="p", device_id="d",
                           service_token="s"))
    store.mark_rejected()
    data = json.loads(
        (tmp_path / "xiaoai_credentials.json").read_text(encoding="utf-8"))
    assert data["rejected"] is True
    assert data["user_id"] == "1"  # 排查信息保留


def test_mark_rejected_no_file(tmp_path):
    store = CredentialStore(data_dir=str(tmp_path))
    store.mark_rejected()  # 不抛异常
    assert not (tmp_path / "xiaoai_credentials.json").exists()


def test_atomic_write_no_tmp_left(tmp_path):
    store = CredentialStore(data_dir=str(tmp_path))
    store.save(Credentials(user_id="1", service_token="s"))
    store.save(Credentials(user_id="2", service_token="s"))
    assert not list(tmp_path.glob("*.tmp"))
    data = json.loads(
        (tmp_path / "xiaoai_credentials.json").read_text(encoding="utf-8"))
    assert data["user_id"] == "2"