"""学期代码：抓取（两条正则）与 jwxt_xnxqdm.json 缓存（语义等价自根 jwxt.py）。"""
import json
import logging
import os
import re
from datetime import datetime

from lit import config

logger = logging.getLogger(__name__)


def _xnxqdm_file():
    return os.path.join(config.DATA_DIR, "jwxt_xnxqdm.json")


def _save_xnxqdm(client):
    try:
        os.makedirs(config.DATA_DIR, exist_ok=True)
        with open(_xnxqdm_file(), "w", encoding="utf-8") as f:
            json.dump({"xnxqdm": client.xnxqdm, "updated": datetime.now().isoformat()}, f)
    except Exception as e:
        logger.warning(f"保存学期代码失败: {e}")


def _load_xnxqdm(client):
    f = _xnxqdm_file()
    if os.path.exists(f):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
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