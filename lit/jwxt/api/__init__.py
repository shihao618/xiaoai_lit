"""教务系统 API 单接口单模块包（C8）。

每个教务系统 API 独立模块，请求构造 + code 校验 + 领域解析内聚同一文件：
- login.py：GET /yzm + POST /new/login（验证码 OCR 与 AES-ECB 为内部协作者）
- term_code.py：POST /new/curMonthXnxq（学期代码 + 周次 + 学期界三合一）
- week_table.py：POST getCalendarWeekDatas（周课表直查 + 当周缓存）

各模块均为 session 注入形态（共享 Session 统一持有，禁止自建连接），
模块间零横向依赖。
"""
from lit.jwxt.api import login, term_code, week_table

__all__ = ["login", "term_code", "week_table"]