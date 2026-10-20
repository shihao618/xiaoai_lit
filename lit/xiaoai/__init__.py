"""小爱音箱服务包：门面类与云端原生播报。

对外导出：
- XiaoAISpeaker：门面类（app.py / tasks.py 调用方契约）
"""

from .speaker import XiaoAISpeaker

__all__ = ["XiaoAISpeaker"]
