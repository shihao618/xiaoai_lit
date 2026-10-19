"""锁定/冷却状态机：失败计数、锁定判定、jwxt_lock.json 持久化（语义等价自根 jwxt.py）。"""
import json
import logging
import os
import re
from datetime import datetime

from lit import config

logger = logging.getLogger(__name__)

MAX_FAILED_ATTEMPTS = 3
COOLDOWN_MINUTES = 30


def _lock_file():
    return os.path.join(config.DATA_DIR, "jwxt", "jwxt_lock.json")


def _save_lock(client):
    try:
        os.makedirs(os.path.dirname(_lock_file()), exist_ok=True)
        data = {
            "account": client.account,
            "lock_until": (client._lock_until or client._cooldown_until).isoformat() if (client._lock_until or client._cooldown_until) else None,
            "failed_attempts": client._failed_attempts,
        }
        with open(_lock_file(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"保存锁定状态失败: {e}")


def _load_lock(client):
    lock_file = _lock_file()
    if os.path.exists(lock_file):
        try:
            with open(lock_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            saved_account = data.get("account", "")
            if not client.account:
                client.account = saved_account
            if saved_account == client.account and data.get("lock_until"):
                client._lock_until = datetime.fromisoformat(data["lock_until"])
                client._failed_attempts = data.get("failed_attempts", 0)
                if datetime.now() < client._lock_until:
                    logger.warning(f"检测到持久化的锁定状态，解锁时间: {client._lock_until}")
                else:
                    client._lock_until = None
        except Exception as e:
            logger.warning(f"加载锁定状态失败: {e}")


def _clear_lock(client):
    client._failed_attempts = 0
    client._cooldown_until = None
    client._lock_until = None
    lock_file = _lock_file()
    if os.path.exists(lock_file):
        try:
            os.remove(lock_file)
        except Exception:
            pass


def _parse_lock_time(message):
    m = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", message)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
        except Exception:
            return None
    return None