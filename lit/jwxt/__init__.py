"""lit.jwxt 包：教务系统客户端（JWXTClient 门面 + 域模块）。"""
from lit.jwxt import term
from lit.jwxt.client import COOLDOWN_MINUTES, JWXTClient, MAX_FAILED_ATTEMPTS

__all__ = ["COOLDOWN_MINUTES", "JWXTClient", "MAX_FAILED_ATTEMPTS", "term"]