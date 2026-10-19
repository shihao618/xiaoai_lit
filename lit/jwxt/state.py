"""教务系统合并状态文件（data/jwxt/jwxt.json）唯一读写方。

六键结构：cookies / login_time / account / xnxqdm / updated / version。
读-合并-写协议保证 cookies 域与 xnxqdm 域各自保存互不覆盖。
"""
import json
import logging
import os

from lit import config

logger = logging.getLogger(__name__)

STATE_FILENAME = "jwxt.json"
STATE_VERSION = 1

LEGACY_COOKIES_FILENAME = "jwxt_cookies.json"
LEGACY_XNXQDM_FILENAME = "jwxt_xnxqdm.json"

_DEFAULT_STATE = {
    "cookies": {},
    "login_time": None,
    "account": "",
    "xnxqdm": "",
    "updated": None,
}


def state_path() -> str:
    return os.path.join(config.DATA_DIR, "jwxt", STATE_FILENAME)


def read_state() -> dict:
    """纯读无副作用；文件不存在 / JSON 损坏 / 顶层非 dict 返回 {}。"""
    path = state_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning("读取教务状态文件失败: %s", e)
        return {}
    if not isinstance(data, dict):
        logger.warning("教务状态文件顶层非对象，忽略")
        return {}
    return data


def update_state(updates: dict) -> None:
    """读-合并-写：仅覆盖 updates 涉及的键，其余键保留；后置六键齐全。"""
    os.makedirs(os.path.dirname(state_path()), exist_ok=True)
    data = dict(_DEFAULT_STATE)
    data.update(read_state())
    data.update(updates)
    data.setdefault("version", STATE_VERSION)
    tmp = state_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, state_path())


def merge_legacy_state() -> bool:
    """存量两旧平铺文件自动合并迁移（幂等）。

    jwxt_cookies.json + jwxt_xnxqdm.json → data/jwxt/jwxt.json。
    新文件已存在 → 忽略旧文件返回 False；两旧文件均不存在 → 不落盘返回 False；
    至少一个存在 → 按域合并（缺失域用类型安全空值）→ 原子写 → 删除读取成功的旧文件。
    某旧文件损坏 → 该域空值、该文件保留、另一域正常迁移。
    """
    if os.path.exists(state_path()):
        return False

    legacy_cookies = os.path.join(config.DATA_DIR, LEGACY_COOKIES_FILENAME)
    legacy_xnxqdm = os.path.join(config.DATA_DIR, LEGACY_XNXQDM_FILENAME)
    if not os.path.exists(legacy_cookies) and not os.path.exists(legacy_xnxqdm):
        return False

    merged = dict(_DEFAULT_STATE)
    removed = []

    if os.path.exists(legacy_cookies):
        try:
            with open(legacy_cookies, "r", encoding="utf-8") as f:
                data = json.load(f)
            merged["cookies"] = data.get("cookies", {})
            merged["login_time"] = data.get("login_time")
            merged["account"] = data.get("account", "")
            removed.append(legacy_cookies)
        except Exception as e:
            logger.warning("旧Cookie文件读取失败，保留待人工处理: %s: %s", legacy_cookies, e)

    if os.path.exists(legacy_xnxqdm):
        try:
            with open(legacy_xnxqdm, "r", encoding="utf-8") as f:
                data = json.load(f)
            merged["xnxqdm"] = data.get("xnxqdm", "")
            merged["updated"] = data.get("updated")
            removed.append(legacy_xnxqdm)
        except Exception as e:
            logger.warning("旧学期代码文件读取失败，保留待人工处理: %s: %s", legacy_xnxqdm, e)

    update_state(merged)
    for p in removed:
        try:
            os.remove(p)
        except OSError as e:
            logger.warning("旧文件删除失败（下次启动重试）: %s: %s", p, e)
    logger.info("教务状态合并迁移: %s + %s → %s",
                LEGACY_COOKIES_FILENAME, LEGACY_XNXQDM_FILENAME, state_path())
    return True