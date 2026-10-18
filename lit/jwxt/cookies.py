"""Cookie 管理：jwxt_cookies.json 持久化、账号匹配加载（语义等价自根 jwxt.py）。"""
import json
import logging
import os
from datetime import datetime

from lit import config

logger = logging.getLogger(__name__)


def _cookie_file():
    return os.path.join(config.DATA_DIR, "jwxt_cookies.json")


def _save_cookies(client):
    os.makedirs(config.DATA_DIR, exist_ok=True)
    cookies = client.session.cookies.get_dict()
    data = {
        "cookies": cookies,
        "login_time": datetime.now().isoformat(),
        "account": client.account,
    }
    with open(_cookie_file(), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    client.cookie_valid = True
    client.login_time = datetime.now()


def _load_cookies(client):
    cookie_file = _cookie_file()
    if os.path.exists(cookie_file):
        try:
            with open(cookie_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            saved_account = data.get("account", "")
            if not client.account:
                client.account = saved_account
            if saved_account == client.account:
                for k, v in data.get("cookies", {}).items():
                    client.session.cookies.set(k, v)
                client.login_time = datetime.fromisoformat(data.get("login_time", datetime.now().isoformat()))
                logger.info(f"加载教务系统缓存Cookie，登录于 {client.login_time}")
                client.cookie_valid = True
            else:
                logger.info(f"Cookie文件账号({saved_account})与当前账号({client.account})不匹配，跳过加载")
        except Exception as e:
            logger.warning(f"加载Cookie失败: {e}")