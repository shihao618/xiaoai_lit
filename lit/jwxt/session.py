"""共享会话模块：Session 统一构造注入 + cookies 域持久化（吸收原 cookies.py 全部职责）。

C8 条文 1：共享 Session 唯一构造点，各 API 模块注入使用、禁止自建连接
（cookie 共享 + 连接池复用）。
"""
import logging
from datetime import datetime

import requests

from lit.jwxt import state

logger = logging.getLogger(__name__)


def create_session(base_url: str) -> requests.Session:
    """构造共享 Session + 默认头（User-Agent / base Referer 与原
    client.__init__ 逐字一致——行为零变化）。"""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": base_url + "/",
    })
    return session


def save_cookies(session: requests.Session, account: str) -> None:
    """cookies/login_time/account 域落盘（读-合并-写协议，语义自原
    cookies.py 零变化；签名从 client 注入改为 session+account 显式传参）。

    内存态副作用（cookie_valid 置位 / login_time 更新）归调用方协作。
    """
    state.update_state({
        "cookies": session.cookies.get_dict(),
        "login_time": datetime.now().isoformat(),
        "account": account,
    })


def load_cookies(session: requests.Session, account: str) -> bool:
    """账号匹配加载（语义自原 cookies.py 零变化，返回是否加载成功）。

    传入 account 为空 → 以落盘账号为匹配基准（原"落盘账号回填内存"
    语义的等价实现，account 写回归调用方协作）；login_time 解析与
    cookie_valid 置位归调用方（读 state 域协作）。
    """
    data = state.read_state()
    if not data:
        return False
    try:
        saved_account = data.get("account", "")
        if not account:
            account = saved_account
        if saved_account == account:
            for k, v in (data.get("cookies") or {}).items():
                session.cookies.set(k, v)
            login_time = data.get("login_time")
            parsed = datetime.fromisoformat(login_time) if login_time else None
            logger.info(f"加载教务系统缓存Cookie，登录于 {parsed}")
            return True
        logger.info(f"Cookie文件账号({saved_account})与当前账号({account})不匹配，跳过加载")
        return False
    except Exception as e:
        logger.warning(f"加载Cookie失败: {e}")
        return False