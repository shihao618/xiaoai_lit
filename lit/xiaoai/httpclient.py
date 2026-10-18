"""安全统一的小米 HTTP 请求层（域名白名单 / 受控重定向 / 响应防御）。"""

import json
import logging
from urllib.parse import urljoin, urlparse

import requests

from . import constants
from .exceptions import (
    NetworkError,
    RequestTimeoutError,
    ResponseFormatError,
    classify_response,
)

logger = logging.getLogger(__name__)

_REDIRECT_STATUSES = (301, 302, 303, 307, 308)


def trusted_url(raw):
    """域名白名单判定（移植 MiCast client.go trustedURL）。

    HTTPS + xiaomi.com/mi.com 及其子域 + 无 userinfo + 端口为空或 443。
    """
    if not raw or not isinstance(raw, str):
        return False
    try:
        u = urlparse(raw)
    except ValueError:
        return False
    if u.scheme != "https":
        return False
    if u.username is not None or u.password is not None:
        return False
    try:
        if u.port is not None and u.port != 443:
            return False
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    if not host:
        return False
    for domain in constants.TRUSTED_DOMAINS:
        if host == domain or host.endswith("." + domain):
            return True
    return False


class XiaomiHTTPClient:
    """对小米服务的全部请求收口：白名单、重定向、响应防御、超时区分。

    requests.Session 不跨线程共享（每次调用自建临时 session，
    需要跨请求共享 cookie jar 时由调用方传入局部 session）。
    """

    def request(self, method, url, *, params=None, form=None, cookies=None,
                timeout=None, session=None, parse_json=True, headers=None):
        if not trusted_url(url):
            raise NetworkError(f"拒绝访问非信任地址: {url}")
        timeout = timeout if timeout is not None else constants.MINA_TIMEOUT
        own_session = session is None
        s = requests.Session() if own_session else session
        try:
            resp = self._send_with_redirects(
                s, method, url,
                params=params, form=form, cookies=cookies, timeout=timeout,
                headers=headers,
            )
            try:
                if not parse_json:
                    return None, resp
                body = self._read_body(resp)
                payload = self._parse_body(body)
                classify_response(resp.status_code, payload)
                return payload, resp
            finally:
                resp.close()
        finally:
            if own_session:
                s.close()

    def _send_with_redirects(self, s, method, url, *, params=None, form=None,
                             cookies=None, timeout=None, headers=None):
        if headers is None:
            headers = {"User-Agent": constants.USER_AGENT}
        current = url
        redirects = 0
        while True:
            try:
                resp = s.request(
                    method, current,
                    params=params if current == url else None,
                    data=form, cookies=cookies, timeout=timeout,
                    allow_redirects=False, stream=True,
                    headers=headers,
                )
            except requests.exceptions.Timeout:
                raise RequestTimeoutError("请求小米服务超时")
            except requests.exceptions.RequestException as e:
                raise NetworkError(f"无法连接小米服务: {e}")
            if resp.status_code in _REDIRECT_STATUSES:
                location = resp.headers.get("Location", "")
                next_url = urljoin(current, location)
                redirects += 1
                resp.close()
                if redirects > constants.MAX_REDIRECTS:
                    raise NetworkError("小米服务重定向次数过多")
                if not trusted_url(next_url):
                    raise NetworkError(f"小米服务重定向到非信任域名: {next_url}")
                current = next_url
                continue
            if resp.status_code != 200:
                resp.close()
                classify_response(resp.status_code, None)
                raise NetworkError(f"小米云端返回 HTTP {resp.status_code}")
            return resp

    def _read_body(self, resp):
        chunks = []
        total = 0
        for chunk in resp.iter_content(chunk_size=65536):
            if not chunk:
                continue
            total += len(chunk)
            if total > constants.MAX_RESPONSE_BYTES:
                raise ResponseFormatError("小米云端回应过大")
            chunks.append(chunk)
        return b"".join(chunks)

    def _parse_body(self, body):
        text = body.decode("utf-8", errors="replace")
        if text.startswith(constants.JSON_PREFIX):
            text = text[len(constants.JSON_PREFIX):]
        try:
            payload = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            logger.warning("小米云端返回非JSON响应: %s", text[:200])
            raise ResponseFormatError("小米云端回应无法识别")
        if not isinstance(payload, dict):
            logger.warning("小米云端返回异常JSON结构: %s", text[:200])
            raise ResponseFormatError("小米云端回应结构无法识别")
        return payload