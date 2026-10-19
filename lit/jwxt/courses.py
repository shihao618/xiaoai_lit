"""课表服务：字段解析（纯函数）、course_table.json 缓存（语义等价自根 jwxt.py）。"""
import json
import os
from datetime import datetime

from lit import config


def parse_courses(raw_courses):
    courses = []
    for item in raw_courses:
        ps = int(item.get("ps", "0"))
        pe = int(item.get("pe", "0"))
        xq = int(item.get("xq", "0"))
        zc_str = item.get("zc", "")
        zc_list = [int(z) for z in zc_str.split(",") if z.strip().isdigit()]
        courses.append({
            "name": item.get("kcmc", ""),
            "section_start": ps,
            "section_end": pe,
            "day": xq,
            "start_time": item.get("qssj", ""),
            "end_time": item.get("jssj", ""),
            "classroom": item.get("jxcdmc", ""),
            "teacher": item.get("teaxms", ""),
            "weeks": zc_list,
            "week_str": zc_str,
        })
    return courses


def _cache_file():
    return os.path.join(config.CACHE_DIR, "jwxt", "course_table.json")


def _save_cache(courses):
    os.makedirs(os.path.dirname(_cache_file()), exist_ok=True)
    with open(_cache_file(), "w", encoding="utf-8") as f:
        json.dump({"updated": datetime.now().isoformat(), "courses": courses}, f, ensure_ascii=False, indent=2)


def _load_cache():
    cache_file = _cache_file()
    if os.path.exists(cache_file):
        with open(cache_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("courses")
    return None