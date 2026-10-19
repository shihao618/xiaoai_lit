"""凭证持久化模块（Q5 方案 A）：data/jwxt/credentials.json 三键明文 + 0o600。

持久化凭证优先（最近成功登录成果，design 2.3.2 定案）；环境变量仅作
全新部署引导态回退。密码绝不出现在任何日志语句（模块级纪律）。
"""
import json
import logging
import os
import stat
from datetime import datetime

from lit import config

logger = logging.getLogger(__name__)


def credentials_path() -> str:
    return os.path.join(config.DATA_DIR, "jwxt", "credentials.json")


def save_credentials(account: str, password: str) -> None:
    """登录成功后调用：三键 {account, password, updated} 原子写
    （tmp + os.replace，与 jwxt.json 同协议）+ os.chmod(0o600)
    （Windows 宿主下 chmod 近似 no-op，见 design 2.5.4）。

    写入 OSError → warning 不阻断（登录主流程已成功；缓存失败
    不影响主链路的处置惯例同 week_table.save_cache）。
    """
    path = credentials_path()
    payload = {
        "account": account,
        "password": password,
        "updated": datetime.now().isoformat(),
    }
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    except OSError as e:
        logger.warning("凭证持久化失败（不影响登录主流程）: %s", e)


def load_credentials() -> tuple[str, str] | None:
    """读取凭证 (account, password)；文件缺失/损坏/JSON 非法/键缺失
    或为空 → None（F11：视为无持久化凭证，回退引导态）。

    权限位过宽仅 warning 不阻断（可移植性：Windows 宿主无 POSIX
    权限语义，校验仅 POSIX 平台执行）。
    """
    path = credentials_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        logger.warning("凭证文件读取失败，视为无持久化凭证: %s", path)
        return None
    if not isinstance(data, dict):
        logger.warning("凭证文件顶层非对象，视为无持久化凭证: %s", path)
        return None
    account = data.get("account")
    password = data.get("password")
    if not isinstance(account, str) or not account:
        return None
    if not isinstance(password, str) or not password:
        return None
    try:
        if os.name == "posix" and stat.S_IMODE(os.stat(path).st_mode) & 0o077:
            logger.warning("凭证文件权限过宽（期望 0o600）: %s", path)
    except OSError:
        pass
    return (account, password)


def resolve_credentials(env_account: str, env_password: str) -> tuple[str, str] | None:
    """凭证优先级判定（design 2.3.2 定案）：

    持久化凭证优先（F13：宿主改环境变量不生效——切换账号请经前端
    登录页或删除 credentials.json）；持久化缺失/损坏 → 环境变量回退
    （F12 引导态）；两者皆无 → None（调用方维持"请先填写账号和密码"）。
    """
    persisted = load_credentials()
    if persisted is not None:
        return persisted
    if env_account and env_password:
        return (env_account, env_password)
    return None