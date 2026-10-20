"""MIoT 原生播报通道测试：签名 / 令牌换取 / 动作参数 / 播放状态解析。"""
import base64
import hashlib
import hmac
import json

import pytest

from lit import config
from lit.xiaoai import miot
from lit.xiaoai.credentials import Credentials


class FakeResponse:
    def __init__(self, content=b"", json_data=None, status_code=200):
        self.content = content
        self._json = json_data
        self.status_code = status_code

    def json(self):
        if self._json is None:
            raise ValueError("no json body")
        return self._json


class FakeSession:
    """最小化 requests.Session 替身：服务登录 + 令牌 + 动作响应。"""

    def __init__(self, login_payload=None, action_payload=None,
                 token="svc-token", token_missing=False):
        self.login_payload = login_payload or {}
        self.action_payload = action_payload or {"code": 0, "result": {"code": 0}}
        self.token = token
        self.token_missing = token_missing
        self.cookies = []
        self.requests = []

    def get(self, url, **kwargs):
        self.requests.append(("GET", url, kwargs))
        if "serviceLogin" in url:
            raw = ("&&&START&&&" + json.dumps(self.login_payload)).encode()
            return FakeResponse(content=raw)
        if not self.token_missing:
            self.cookies = [type("C", (), {"name": "serviceToken",
                                           "value": self.token})()]
        return FakeResponse(content=b"")

    def post(self, url, **kwargs):
        self.requests.append(("POST", url, kwargs))
        return FakeResponse(json_data=self.action_payload)


SECURITY = base64.b64encode(b"0123456789abcdef").decode()


def make_cred():
    return Credentials(user_id="42", pass_token="pt", device_id="dev-1",
                       service_token="st")


def login_payload():
    return {"code": 0, "ssecurity": SECURITY, "nonce": "123456",
            "location": "https://sts.api.io.mi.com/sts?d=abc"}


# ---------- 签名 ----------


def test_sign_data_structure_and_signature():
    signed = miot.sign_data("/miotspec/action",
                            {"params": {"did": "1"}}, SECURITY)
    assert set(signed) == {"_nonce", "data", "signature"}
    assert json.loads(signed["data"]) == {"params": {"did": "1"}}
    # 用返回的 nonce 重新计算签名，验证一致性
    snonce = base64.b64encode(hashlib.sha256(
        base64.b64decode(SECURITY)
        + base64.b64decode(signed["_nonce"])).digest()).decode()
    msg = "&".join(["/miotspec/action", snonce, signed["_nonce"],
                    "data=" + signed["data"]])
    expected = base64.b64encode(hmac.new(
        base64.b64decode(snonce), msg.encode("utf-8"),
        hashlib.sha256).digest()).decode()
    assert signed["signature"] == expected


def test_parse_account_payload_prefix_and_plain():
    assert miot.parse_account_payload(
        b"&&&START&&&{\"code\":0}") == {"code": 0}
    assert miot.parse_account_payload(b'{"code":1}') == {"code": 1}
    assert miot.parse_account_payload(b"") == {}


# ---------- 动作参数 ----------


def test_play_text_params(monkeypatch):
    client = miot.MiOTClient(session=FakeSession())
    recorded = []
    monkeypatch.setattr(client, "_call",
                        lambda cred, uri, params, retry=True:
                        recorded.append((uri, params)) or {"code": 0})
    client.play_text(make_cred(), "800740573", "你好")
    uri, params = recorded[0]
    assert uri == "/miotspec/action"
    assert params == {"params": {"did": "800740573", "siid": 5, "aiid": 3,
                                 "in": ["你好"]}}


def test_execute_text_params(monkeypatch):
    client = miot.MiOTClient(session=FakeSession())
    recorded = []
    monkeypatch.setattr(client, "_call",
                        lambda cred, uri, params, retry=True:
                        recorded.append(params) or {"code": 0})
    client.execute_text(make_cred(), "800740573", "查询天气", with_response=False)
    assert recorded[0]["params"]["in"] == ["查询天气", 0]


def test_stop_params_uses_pause(monkeypatch):
    """设备固件拒绝 Stop 动作，停止接口实际发送 Pause（siid=3/aiid=3）。"""
    client = miot.MiOTClient(session=FakeSession())
    recorded = []
    monkeypatch.setattr(client, "_call",
                        lambda cred, uri, params, retry=True:
                        recorded.append(params) or {"code": 0})
    client.stop(make_cred(), "800740573")
    assert recorded[0]["params"] == {"did": "800740573", "siid": 3,
                                     "aiid": 3, "in": []}


def test_playing_state_parses_value(monkeypatch):
    client = miot.MiOTClient(session=FakeSession())
    monkeypatch.setattr(client, "_call", lambda *a, **k: {
        "code": 0, "result": [{"did": "1", "siid": 3, "piid": 1,
                               "code": 0, "value": 1}]})
    assert client.playing_state(make_cred(), "1") == 1


# ---------- 令牌换取与缓存 ----------


def test_exchange_token_and_play(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(miot.constants, "MIOT_BASE_URL", "https://api.io.mi.com/app")
    session = FakeSession(login_payload=login_payload())
    client = miot.MiOTClient(session=session)
    cred = make_cred()

    result = client.play_text(cred, "800740573", "你好")
    assert result["code"] == 0

    assert client._token == ("42", SECURITY, "svc-token")
    # 网络请求序列：serviceLogin → 跟随 location → miotspec/action
    methods = [r[0] for r in session.requests]
    assert methods == ["GET", "GET", "POST"]
    post = [r for r in session.requests if r[0] == "POST"][0]
    assert post[1] == "https://api.io.mi.com/app/miotspec/action"
    assert post[2]["cookies"] == {"userId": "42",
                                  "serviceToken": "svc-token"}
    body = post[2]["data"]
    assert set(body) == {"_nonce", "data", "signature"}
    assert json.loads(body["data"])["params"]["did"] == "800740573"
    # 令牌落盘 0600（POSIX 下）
    token_file = tmp_path / "xiaoai" / "miot_token.json"
    assert token_file.is_file()
    assert json.loads(token_file.read_text(encoding="utf-8"))["service_token"] \
        == "svc-token"


def test_cached_token_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    session = FakeSession(login_payload=login_payload())
    cred = make_cred()
    first = miot.MiOTClient(session=session)
    first.ensure_token(cred)
    assert session.requests

    # 新实例：应直接读缓存，不再请求 serviceLogin
    session2 = FakeSession(login_payload={"code": 999})
    second = miot.MiOTClient(session=session2)
    token = second.ensure_token(cred)
    assert token == ("42", SECURITY, "svc-token")
    assert [r for r in session2.requests if "serviceLogin" in r[1]] == []


def test_login_failure_raises_credential_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    session = FakeSession(login_payload={"code": 70016})
    client = miot.MiOTClient(session=session)
    with pytest.raises(miot.CredentialRejectedError):
        client.ensure_token(make_cred())


def test_action_rejected_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    session = FakeSession(login_payload=login_payload(),
                          action_payload={"code": 0,
                                          "result": {"code": -1}})
    client = miot.MiOTClient(session=session)
    with pytest.raises(miot.CommandRejectedError):
        client.play_text(make_cred(), "800740573", "你好")
