"""小爱音箱语音播报服务包（替代存量 xiaoai.py 单文件模块）。

对外导出：
- XiaoAISpeaker：门面类（app.py / scheduler.py / cookie_manager.py 调用方契约）
- run_async：兼容垫片（存量调用方以协程语义调用的地方零改动切换）
"""

import asyncio
import inspect

from .speaker import XiaoAISpeaker

__all__ = ["XiaoAISpeaker", "run_async"]


def run_async(value):
    """兼容垫片：入参为协程对象时以临时事件循环执行并返回结果；
    否则（同步方法已求值的结果）直接返回入参本身。

    使 run_async(self.xiaoai.check_cookie_valid())（cookie_manager.py，
    入参已是同步求值结果）与 run_async(xiaoai_speaker.get_devices())
    （app.py）在同步实现下零改动工作。
    """
    if inspect.iscoroutine(value):
        return asyncio.run(value)
    return value