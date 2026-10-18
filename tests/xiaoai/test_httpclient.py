import pytest
import requests

from lit.xiaoai import constants
from lit.xiaoai.exceptions import (
    CommandRejectedError,
    CredentialRejectedError,
    NetworkError,
    RateLimitedError,
    RequestTimeoutError,
    ResponseFormatError,
    classify_response,
)
from lit.xiaoai.httpclient import XiaomiHTTPClient, trusted_url

HTTPS_OK = "https://account.xiaomi.com/x"


class FakeResponse:
    def __init__(self, status_code=200, body=b"", headers=None, url=""):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}
        self.url = url
        self.cookies = requests.cookies.RequestsCookieJar()
        self.closed = False

    def iter_content(self, chunk_size=65536):
        if self._body:
            yield self._body

    def close(self):
        self.closed = True


def install_fake(monkeypatch, handler):
    calls = []

    def fake_request(self, method, url, **kwargs):
        calls.append({"method": method, "url": url, **kwargs})
        return handler(method, url, kwargs)

    monkeypatch.setattr(requests.Session, "request", fake_request)
    return calls


# ---------- classify_response（异常分类器） ----------

def test_classify_429():
    with pytest.raises(RateLimitedError):
        classify_response(429, None)


def test_classify_403():
    with pytest.raises(RateLimitedError):
        classify_response(403, None)


def test_classify_70016():
    with pytest.raises(CredentialRejectedError):
        classify_response(200, {"code": 70016})


def test_classify_http_401_rejected():
    # api2.mina.mi.com 对失效 serviceToken 实测返回 HTTP 401（非 JSON 70016）
    with pytest.raises(CredentialRejectedError):
        classify_response(401, None)


def test_classify_70001():
    with pytest.raises(CredentialRejectedError):
        classify_response(200, {"code": 70001})


def test_classify_70022():
    with pytest.raises(RateLimitedError):
        classify_response(200, {"code": 70022})


def test_classify_top_level_unknown_code():
    with pytest.raises(CommandRejectedError):
        classify_response(200, {"code": 401})


def test_classify_inner_code():
    with pytest.raises(CommandRejectedError):
        classify_response(200, {"code": 0, "data": {"code": -500}})


def test_classify_ok():
    assert classify_response(200, {"code": 0}) == {"code": 0}


# ---------- 域名白名单 ----------

@pytest.mark.parametrize("url", [
    "https://evil.com",
    "http://account.xiaomi.com",
    "https://xiaomi.com.evil.com",
    "https://user:pass@account.xiaomi.com",
    "https://account.xiaomi.com:8443/x",
])
def test_trusted_url_rejects(url):
    assert not trusted_url(url)


@pytest.mark.parametrize("url", [
    "https://account.xiaomi.com/x",
    "https://api2.mina.mi.com/x",
    "https://sts.api.io.mi.com/sts",
    "https://xiaomi.com/",
    "https://api.io.mi.com:443/x",
])
def test_trusted_url_allows(url):
    assert trusted_url(url)


def test_untrusted_url_rejected_without_request(monkeypatch):
    calls = install_fake(monkeypatch, lambda m, u, kw: FakeResponse())
    with pytest.raises(NetworkError):
        XiaomiHTTPClient().request("GET", "https://evil.com/x")
    assert calls == []


# ---------- 响应解析 ----------

def test_json_prefix_stripped(monkeypatch):
    install_fake(monkeypatch, lambda m, u, kw: FakeResponse(
        body=b'&&&START&&&{"code":0}'))
    payload, _ = XiaomiHTTPClient().request("GET", HTTPS_OK)
    assert payload == {"code": 0}


def test_plain_json(monkeypatch):
    install_fake(monkeypatch, lambda m, u, kw: FakeResponse(body=b'{"code":0}'))
    payload, _ = XiaomiHTTPClient().request("GET", HTTPS_OK)
    assert payload == {"code": 0}


def test_non_json_rejected(monkeypatch):
    install_fake(monkeypatch, lambda m, u, kw: FakeResponse(body=b"<html>x"))
    with pytest.raises(ResponseFormatError):
        XiaomiHTTPClient().request("GET", HTTPS_OK)


def test_empty_body_rejected(monkeypatch):
    install_fake(monkeypatch, lambda m, u, kw: FakeResponse(body=b""))
    with pytest.raises(ResponseFormatError):
        XiaomiHTTPClient().request("GET", HTTPS_OK)


def test_non_dict_json_rejected(monkeypatch):
    install_fake(monkeypatch, lambda m, u, kw: FakeResponse(body=b"[1,2]"))
    with pytest.raises(ResponseFormatError):
        XiaomiHTTPClient().request("GET", HTTPS_OK)


def test_response_too_large(monkeypatch):
    big = b"x" * (constants.MAX_RESPONSE_BYTES + 1)
    install_fake(monkeypatch, lambda m, u, kw: FakeResponse(body=big))
    with pytest.raises(ResponseFormatError):
        XiaomiHTTPClient().request("GET", HTTPS_OK)


# ---------- 状态码与网络错误分类 ----------

@pytest.mark.parametrize("status,exc", [
    (429, RateLimitedError),
    (403, RateLimitedError),
    (500, NetworkError),
    (404, NetworkError),
])
def test_status_classification(monkeypatch, status, exc):
    install_fake(monkeypatch, lambda m, u, kw: FakeResponse(status_code=status))
    with pytest.raises(exc):
        XiaomiHTTPClient().request("GET", HTTPS_OK)


def test_timeout_classification(monkeypatch):
    def fake_request(self, method, url, **kw):
        raise requests.exceptions.Timeout("boom")

    monkeypatch.setattr(requests.Session, "request", fake_request)
    with pytest.raises(RequestTimeoutError):
        XiaomiHTTPClient().request("GET", HTTPS_OK)


def test_connection_error_classification(monkeypatch):
    def fake_request(self, method, url, **kw):
        raise requests.exceptions.ConnectionError("boom")

    monkeypatch.setattr(requests.Session, "request", fake_request)
    with pytest.raises(NetworkError):
        XiaomiHTTPClient().request("GET", HTTPS_OK)


# ---------- 重定向 ----------

def test_redirect_to_untrusted_rejected(monkeypatch):
    def handler(m, u, kw):
        if "start" in u:
            return FakeResponse(status_code=302,
                                headers={"Location": "https://evil.com/x"})
        return FakeResponse(body=b"{}")

    install_fake(monkeypatch, handler)
    with pytest.raises(NetworkError):
        XiaomiHTTPClient().request("GET", "https://account.xiaomi.com/start")


def test_redirect_within_whitelist(monkeypatch):
    def handler(m, u, kw):
        if u == "https://account.xiaomi.com/start":
            return FakeResponse(
                status_code=302,
                headers={"Location": "https://api.io.mi.com/next"})
        return FakeResponse(body=b'{"code":0}')

    install_fake(monkeypatch, handler)
    payload, _ = XiaomiHTTPClient().request("GET",
                                            "https://account.xiaomi.com/start")
    assert payload == {"code": 0}


def test_too_many_redirects(monkeypatch):
    def handler(m, u, kw):
        return FakeResponse(status_code=302,
                            headers={"Location": "https://account.xiaomi.com/loop"})

    install_fake(monkeypatch, handler)
    with pytest.raises(NetworkError):
        XiaomiHTTPClient().request("GET", "https://account.xiaomi.com/loop")