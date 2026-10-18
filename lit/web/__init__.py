"""lit.web 包：Flask 应用工厂与蓝图分组（D6，不设 __main__ 启动块）。"""
from lit.web.app import create_app

__all__ = ["create_app"]