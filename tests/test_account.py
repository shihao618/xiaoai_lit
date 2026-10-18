import base64
import hashlib
import types
from urllib.parse import quote

import pytest

from xiaoai import constants
from xiaoai.account import XiaomiAccountClient, login_device_id
from xiaoai.exceptions import (
    CredentialRejectedError,
    RateLimitedError,
    RequestTimeoutError,
    ResponseFormatError,
)


class StubHTTP:
    def __init__(self, handler=None):
        self.calls = []
        self.handler = handler

    def request(self, method, url, *, params=None, form=None, cookies=None,
                timeout=None, session=None, parse_json=True):
        call = {"method": method, "url": url, "params": params, "form": form,
                "cookies": cookies, "timeout": timeout, "session": session,
                "parse_json": parse_json}
        self.calls.append(call)
        if self.handler:
            return self.handler(call)
        return {}, None


# ---------- login_device_id ----------

def test_login_device_id_stable():
    assert login_device_id("123") == login_device_id("123")
    assert len(login_device_id("123")) == 16
    assert login_device_id("1") != login_device_id("2")


# ---------- start_qr ----------

def test_start_qr_params_and_result():
    def handler(call):
        return ({"loginUrl": "https://account.xiaomi.com/qr?ticket=t",
                 "lp": "https://account.xiaomi.com/lp?x=1"}, None)

    http = StubHTTP(handler=handler)
    login_url, poll_url, device_id = XiaomiAccountClient(http).start_qr()
    assert login_url == "https://account.xiaomi.com/qr?ticket=t"
    assert poll_url == "https://account.xiaomi.com/lp?x=1"
    assert len(device_id) == 16
    call = http.calls[0]
    assert call["url"] == constants.ACCOUNT_BASE_URL + "/longPolling/loginUrl"
    assert call["params"]["sid"] == "xiaomiio"
    assert call["params"]["qs"] == "%3Fsid%3Dxiaomiio%26_json%3Dtrue"
    assert call["params"]["callback"] == "https://sts.api.io.mi.com/sts"
    assert call["params"]["_qrsize"] == "480"
    assert call["params"]["_locale"] == "zh_CN"
    assert call["cookies"]["sdkVersion"] == constants.SDK_VERSION
    assert call["cookies"]["deviceId"] == device_id
    assert call["timeout"] == constants.QR_START_TIMEOUT


def test_start_qr_rejects_untrusted_login_url():
    http = StubHTTP(handler=lambda c: (
        {"loginUrl": "https://evil.com/x",
         "lp": "https://account.xiaomi.com/lp"}, None))
    with pytest.raises(ResponseFormatError):
        XiaomiAccountClient(http).start_qr()


def test_start_qr_missing_fields():
    http = StubHTTP(handler=lambda c: ({"code": 0}, None))
    with pytest.raises(ResponseFormatError):
        XiaomiAccountClient(http).start_qr()


# ---------- poll_qr 四态 ----------

def poll_with(payload_or_exc):
    def handler(call):
        if isinstance(payload_or_exc, Exception):
            raise payload_or_exc
        return payload_or_exc, None

    return XiaomiAccountClient(StubHTTP(handler=handler))


POLL_URL = "https://account.xiaomi.com/lp?x=1"


def test_poll_waiting():
    r = poll_with({"code": 0}).poll_qr(POLL_URL)
    assert r.status == "waiting"
    assert r.next_poll_url == POLL_URL


def test_poll_waiting_limited_on_429():
    r = poll_with(RateLimitedError("HTTP 429")).poll_qr(POLL_URL)
    assert r.status == "waiting_limited"


def test_poll_timeout_treated_as_waiting():
    r = poll_with(RequestTimeoutError("timeout")).poll_qr(POLL_URL)
    assert r.status == "waiting"


def test_poll_expired_70016():
    r = poll_with({"code": 70016}).poll_qr(POLL_URL)
    assert r.status == "expired"


def test_poll_expired_70016_string_code():
    r = poll_with({"code": "70016"}).poll_qr(POLL_URL)
    assert r.status == "expired"


def test_poll_confirmed():
    r = poll_with({"passToken": "v2:xxx", "userId": 123456}).poll_qr(POLL_URL)
    assert r.status == "confirmed"
    assert r.user_id == "123456"
    assert r.pass_token == "v2:xxx"


def test_poll_confirmed_missing_user():
    with pytest.raises(ResponseFormatError):
        poll_with({"passToken": "v2:xxx"}).poll_qr(POLL_URL)


def test_poll_new_lp_updates():
    r = poll_with({"lp": "https://account.xiaomi.com/lp2"}).poll_qr(POLL_URL)
    assert r.status == "waiting"
    assert r.next_poll_url == "https://account.xiaomi.com/lp2"


def test_poll_new_lp_untrusted_rejected():
    with pytest.raises(ResponseFormatError):
        poll_with({"lp": "https://evil.com/lp2"}).poll_qr(POLL_URL)


def test_poll_untrusted_url_rejected():
    with pytest.raises(ResponseFormatError):
        poll_with({"code": 0}).poll_qr("https://evil.com/lp")


# ---------- exchange ----------

def make_exchange_handler(second_resp=None, first_payload=None):
    def handler(call):
        if call["parse_json"]:
            if first_payload is not None:
                return first_payload, None
            return {"code": 0, "ssecurity": "sec", "nonce": "n1",
                    "location": "https://sts.api.io.mi.com/sts?ticket=abc"}, None
        return None, second_resp
    return handler


def test_exchange_success_sign_and_cookie():
    resp = types.SimpleNamespace(
        cookies=[types.SimpleNamespace(name="serviceToken", value="srv-token",
                                       domain="sts.api.io.mi.com")],
        url="https://sts.api.io.mi.com/sts",
    )
    http = StubHTTP(handler=make_exchange_handler(second_resp=resp))
    token = XiaomiAccountClient(http).exchange("42", "v2:pt", "dev123")

    assert token["service_token"] == "srv-token"
    assert token["security"] == "sec"
    assert token["user_id"] == "42"
    assert token["pass_token"] == "v2:pt"
    assert token["device_id"] == "dev123"

    first = http.calls[0]
    assert first["url"] == constants.ACCOUNT_BASE_URL + "/pass/serviceLogin"
    assert first["params"] == {"sid": "micoapi", "_json": "true"}
    assert first["cookies"]["userId"] == "42"
    assert first["cookies"]["passToken"] == "v2:pt"
    assert first["cookies"]["deviceId"] == "dev123"
    assert first["cookies"]["sdkVersion"] == constants.SDK_VERSION

    second = http.calls[1]
    expected_sign = base64.b64encode(
        hashlib.sha1(b"nonce=n1&sec").digest()).decode("ascii")
    assert second["url"] == ("https://sts.api.io.mi.com/sts?ticket=abc"
                             "&clientSign=" + quote(expected_sign, safe=""))
    assert second["parse_json"] is False


def test_exchange_service_token_from_jar_fallback():
    def handler(call):
        if call["parse_json"]:
            call["session"].cookies.set("serviceToken", "jar-token",
                                        domain="sts.api.io.mi.com")
            return {"code": 0, "ssecurity": "sec", "nonce": "n1",
                    "location": "https://sts.api.io.mi.com/sts"}, None
        resp = types.SimpleNamespace(cookies=[], url="https://sts.api.io.mi.com/sts")
        return None, resp

    http = StubHTTP(handler=handler)
    token = XiaomiAccountClient(http).exchange("42", "pt", "dev")
    assert token["service_token"] == "jar-token"


def test_exchange_no_service_token():
    resp = types.SimpleNamespace(cookies=[], url="https://sts.api.io.mi.com/sts")
    http = StubHTTP(handler=make_exchange_handler(second_resp=resp))
    with pytest.raises(ResponseFormatError):
        XiaomiAccountClient(http).exchange("42", "pt", "dev")


def test_exchange_rate_limited_70022():
    http = StubHTTP(handler=make_exchange_handler(
        first_payload={"code": 70022}))
    with pytest.raises(RateLimitedError):
        XiaomiAccountClient(http).exchange("42", "pt", "dev")


def test_exchange_rejected_70016():
    http = StubHTTP(handler=make_exchange_handler(
        first_payload={"code": 70016}))
    with pytest.raises(CredentialRejectedError):
        XiaomiAccountClient(http).exchange("42", "pt", "dev")


def test_exchange_rejected_70001():
    http = StubHTTP(handler=make_exchange_handler(
        first_payload={"code": 70001}))
    with pytest.raises(CredentialRejectedError):
        XiaomiAccountClient(http).exchange("42", "pt", "dev")


def test_exchange_untrusted_location():
    http = StubHTTP(handler=make_exchange_handler(
        first_payload={"code": 0, "ssecurity": "s", "nonce": "n",
                       "location": "https://evil.com/x"}))
    with pytest.raises(ResponseFormatError):
        XiaomiAccountClient(http).exchange("42", "pt", "dev")


def test_exchange_missing_fields():
    http = StubHTTP(handler=make_exchange_handler(
        first_payload={"code": 0, "ssecurity": "s"}))
    with pytest.raises(ResponseFormatError):
        XiaomiAccountClient(http).exchange("42", "pt", "dev")