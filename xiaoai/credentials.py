"""凭证数据模型与本地持久化（三格式加载链 / 原子写入 / 失效标记）。"""

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import config

logger = logging.getLogger(__name__)

CREDENTIALS_FILENAME = "xiaoai_credentials.json"
MI_TOKEN_FILENAME = "mi.token"
XIAOAI_COOKIES_FILENAME = "xiaoai_cookies.json"
XIAOAI_TOKEN_FILENAME = "xiaoai_token.json"


@dataclass
class Credentials:
    user_id: str = ""
    pass_token: str = ""
    device_id: str = ""
    service_token: str = ""
    security: Optional[str] = None
    refreshed_at: Optional[datetime] = None
    rejected: bool = False
    default_device_id: Optional[str] = None

    def is_usable(self) -> bool:
        return (not self.rejected) and bool(self.user_id) and bool(self.service_token)


class CredentialStore:
    """凭证持久化：data/xiaoai_credentials.json，snake_case 键、UTF-8、原子写。"""

    def __init__(self, data_dir: Optional[str] = None):
        self._data_dir = data_dir or config.DATA_DIR

    @property
    def path(self) -> str:
        return os.path.join(self._data_dir, CREDENTIALS_FILENAME)

    def load(self) -> Optional[Credentials]:
        """三格式加载链：新格式 → mi.token → 手动 Cookie。

        任一格式解析失败跳过并尝试下一格式；三类文件均不存在返回 None。
        xiaoai_token.json 的 device_id 迁移为 default_device_id。
        """
        cred = self._load_new_format()
        if cred is None:
            cred = self._load_mi_token()
        if cred is None:
            cred = self._load_manual_cookies()
        if cred is None:
            return None
        if not cred.default_device_id:
            cred.default_device_id = self._load_legacy_default_device()
        return cred

    def save(self, cred: Credentials) -> None:
        os.makedirs(self._data_dir, exist_ok=True)
        data = {
            "user_id": cred.user_id,
            "pass_token": cred.pass_token,
            "device_id": cred.device_id,
            "service_token": cred.service_token,
            "security": cred.security or "",
            "refreshed_at": (cred.refreshed_at or datetime.now()).isoformat(),
            "rejected": cred.rejected,
            "default_device_id": cred.default_device_id or "",
        }
        tmp_path = self.path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        if os.name != "nt":
            try:
                os.chmod(tmp_path, 0o600)
            except OSError:
                pass
        os.replace(tmp_path, self.path)

    def mark_rejected(self) -> None:
        """将 rejected=true 写回凭证文件（保留排查信息，不物理删除）。"""
        if not os.path.exists(self.path):
            logger.warning("凭证文件不存在，跳过失效标记")
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("读取凭证文件失败，跳过失效标记: %s", e)
            return
        if not isinstance(data, dict):
            return
        data["rejected"] = True
        tmp_path = self.path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self.path)

    def _read_json(self, filename: str) -> Optional[dict]:
        path = os.path.join(self._data_dir, filename)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("凭证文件 %s 解析失败，跳过: %s", filename, e)
            return None

    def _load_new_format(self) -> Optional[Credentials]:
        data = self._read_json(CREDENTIALS_FILENAME)
        if data is None:
            return None
        cred = Credentials(
            user_id=str(data.get("user_id", "") or ""),
            pass_token=str(data.get("pass_token", "") or ""),
            device_id=str(data.get("device_id", "") or ""),
            service_token=str(data.get("service_token", "") or ""),
            security=data.get("security") or None,
            rejected=bool(data.get("rejected", False)),
            default_device_id=data.get("default_device_id") or None,
        )
        refreshed = data.get("refreshed_at")
        if refreshed:
            try:
                cred.refreshed_at = datetime.fromisoformat(refreshed)
            except ValueError:
                cred.refreshed_at = None
        logger.info("已加载小爱凭证文件（新格式）")
        return cred

    def _load_mi_token(self) -> Optional[Credentials]:
        data = self._read_json(MI_TOKEN_FILENAME)
        if data is None:
            return None
        user_id = str(data.get("userId", "") or "")
        service_token = str(data.get("serviceToken", "") or "")
        if not user_id and not service_token:
            return None
        cred = Credentials(
            user_id=user_id,
            pass_token=str(data.get("passToken", "") or ""),
            device_id=str(data.get("deviceId", "") or ""),
            service_token=service_token,
            security=data.get("ssecurity") or None,
        )
        logger.info("已加载小爱凭证文件（mi.token 存量格式）")
        return cred

    def _load_manual_cookies(self) -> Optional[Credentials]:
        data = self._read_json(XIAOAI_COOKIES_FILENAME)
        if data is None:
            return None
        cookies = data.get("cookies")
        if not isinstance(cookies, dict):
            return None
        user_id = str(cookies.get("userId", "") or "")
        service_token = str(cookies.get("serviceToken", "") or "")
        if not user_id or not service_token:
            return None
        cred = Credentials(
            user_id=user_id,
            pass_token="",
            device_id=str(cookies.get("deviceId", "") or ""),
            service_token=service_token,
        )
        logger.info("已加载小爱凭证文件（手动 Cookie 降级格式，仅 serviceToken 可用）")
        return cred

    def _load_legacy_default_device(self) -> Optional[str]:
        """存量 xiaoai_token.json 的 device_id 语义为已选默认音箱。"""
        data = self._read_json(XIAOAI_TOKEN_FILENAME)
        if data is None:
            return None
        return data.get("device_id") or None