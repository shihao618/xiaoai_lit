"""统一异常体系与错误分类器（唯一错误分类入口）。"""

from . import constants


class XiaoAIError(Exception):
    """小爱服务基础异常。"""


class NetworkError(XiaoAIError):
    """连接失败 / DNS 错误 / 非 200 的服务端临时故障（不迁移凭证状态）。"""


class RateLimitedError(XiaoAIError):
    """限频：HTTP 429/403 或 JSON code=70022（不迁移凭证状态）。"""


class CredentialRejectedError(XiaoAIError):
    """凭证被明确拒绝：JSON code=70016/70001（迁移至 REJECTED）。"""


class RequestTimeoutError(XiaoAIError):
    """请求超时（结果不确定，播放类禁止重发）。"""


class CommandRejectedError(XiaoAIError):
    """指令被服务明确拒绝：MiNA 顶层非 0 code 或 ubus 内层 data.code!=0。"""


class ResponseFormatError(XiaoAIError):
    """响应无法识别：非 JSON / 超 2MB / 关键字段缺失。"""


class QRSessionError(XiaoAIError):
    """扫码登录会话不存在或已作废。"""


def as_int(value):
    """宽松整数转换（JSON number/str → int，失败返回 None）。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def classify_response(status_code, payload=None):
    """统一错误分类入口。

    判定顺序：HTTP 429/403 → 限频；HTTP 401 → 凭证被拒
    （api2.mina.mi.com 对失效 serviceToken 实测返回 401 而非 JSON 70016）；
    非 200 → 网络错误；JSON 顶层 code（70016/70001 → 凭证被拒；
    70022 → 限频；其他非 0 → 指令被拒）；ubus 内层 data.code != 0 → 指令被拒。
    """
    if status_code in (429, 403):
        raise RateLimitedError(f"小米服务请求受限（HTTP {status_code}），请稍后重试")
    if status_code == 401:
        raise CredentialRejectedError("小米账号凭证已失效，请重新登录")
    if status_code != 200:
        raise NetworkError(f"小米云端返回 HTTP {status_code}")
    if payload is None:
        return None
    code = as_int(payload.get("code"))
    if code in (constants.CODE_CREDENTIAL_EXPIRED, constants.CODE_CREDENTIAL_REJECTED):
        raise CredentialRejectedError("小米账号凭证已失效，请重新登录")
    if code == constants.CODE_RATE_LIMITED:
        raise RateLimitedError("小米账号请求过于频繁，请稍后重试")
    if code is not None and code != 0:
        raise CommandRejectedError(f"小米服务请求失败（code={code}）")
    data = payload.get("data")
    if isinstance(data, dict):
        inner = as_int(data.get("code"))
        if inner is not None and inner != 0:
            raise CommandRejectedError(f"音箱未接受指令（code={inner}）")
    return payload