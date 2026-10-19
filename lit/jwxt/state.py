"""教务系统合并状态文件（data/jwxt/jwxt.json）唯一读写方。

五键结构：cookies / login_time / account / xnxqdm / updated。
读-合并-写协议保证 cookies 域与 xnxqdm 域各自保存互不覆盖。
"""
import json
import logging
import os

from lit import config

logger = logging.getLogger(__name__)

STATE_FILENAME = "jwxt.json"

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
    """读-合并-写：仅覆盖 updates 涉及的键，其余键保留；后置五键齐全。"""
    os.makedirs(os.path.dirname(state_path()), exist_ok=True)
    data = dict(_DEFAULT_STATE)
    data.update(read_state())
    data.update(updates)
    tmp = state_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, state_path())