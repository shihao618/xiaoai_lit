import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest


@pytest.fixture
def isolated_dirs(tmp_path, monkeypatch):
    """隔离数据/缓存目录：jwxt 补测不触碰真实 data/ 与 cache/。"""
    data_dir = tmp_path / "data"
    cache_dir = tmp_path / "cache"
    data_dir.mkdir()
    cache_dir.mkdir()
    import config
    monkeypatch.setattr(config, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(config, "CACHE_DIR", str(cache_dir))
    return {"data": str(data_dir), "cache": str(cache_dir)}