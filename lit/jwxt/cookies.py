"""Cookie 管理：jwxt.json cookies 域持久化、账号匹配加载（语义等价自根 jwxt.py）。"""
import logging
from datetime import datetime

from lit.jwxt import state

logger = logging.getLogger(__name__)


def _save_cookies(client):
    state.update_state({
        "cookies": client.session.cookies.get_dict(),
        "login_time": datetime.now().isoformat(),
        "account": client.account,
    })
    client.cookie_valid = True
    client.login_time = datetime.now()


def _load_cookies(client):
    data = state.read_state()
    if not data:
        return
    try:
        saved_account = data.get("account", "")
        if not client.account:
            client.account = saved_account
        if saved_account == client.account:
            for k, v in (data.get("cookies") or {}).items():
                client.session.cookies.set(k, v)
            login_time = data.get("login_time")
            if login_time:
                client.login_time = datetime.fromisoformat(login_time)
            logger.info(f"加载教务系统缓存Cookie，登录于 {client.login_time}")
            client.cookie_valid = True
        else:
            logger.info(f"Cookie文件账号({saved_account})与当前账号({client.account})不匹配，跳过加载")
    except Exception as e:
        logger.warning(f"加载Cookie失败: {e}")