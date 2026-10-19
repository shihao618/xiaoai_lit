"""学期代码：抓取（两条正则）与 jwxt.json xnxqdm 域缓存（语义等价自根 jwxt.py）。"""
import logging
import re
from datetime import datetime

from lit.jwxt import state

logger = logging.getLogger(__name__)


def _save_xnxqdm(client):
    try:
        state.update_state({"xnxqdm": client.xnxqdm, "updated": datetime.now().isoformat()})
    except Exception as e:
        logger.warning(f"保存学期代码失败: {e}")


def _load_xnxqdm(client):
    try:
        data = state.read_state()
        client.xnxqdm = data.get("xnxqdm", "")
        if client.xnxqdm:
            logger.info(f"加载缓存学期代码: {client.xnxqdm}")
    except Exception as e:
        logger.warning(f"加载学期代码失败: {e}")


def _fetch_xnxqdm(client):
    try:
        resp = client.session.get(
            f"{client.base_url}/new/student/xsgrkb/week.page",
            timeout=15,
        )
        html = resp.text
        m = re.search(r"""<option[^>]*value=['"](\d{6,})['"][^>]*selected""", html)
        if m:
            client.xnxqdm = m.group(1)
            _save_xnxqdm(client)
            logger.info(f"自动获取学期代码: {client.xnxqdm}")
            return True
        m = re.search(r"""xnxqdm['"]\s*[:=]\s*['"](\d{6,})['"]""", html)
        if m:
            client.xnxqdm = m.group(1)
            _save_xnxqdm(client)
            logger.info(f"自动获取学期代码(从变量): {client.xnxqdm}")
            return True
        logger.warning("无法从课表页面解析学期代码")
        return False
    except Exception as e:
        logger.warning(f"获取学期代码失败: {e}")
        return False