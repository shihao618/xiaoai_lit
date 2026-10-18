import os
import sys
import types

import pytest

from xiaoai import tts


@pytest.fixture
def tts_env(tmp_path, monkeypatch):
    monkeypatch.setattr("config.DATA_DIR", str(tmp_path))
    monkeypatch.setattr("config.XIAOAI_TTS_BASE_URL", "http://192.168.0.9:5000")
    return tmp_path


def _install_fake_edge_tts(monkeypatch, save_impl):
    async def _save(self, path):
        save_impl(path)

    mod = types.ModuleType("edge_tts")
    mod.Communicate = type(
        "Communicate", (), {"__init__": lambda self, text, voice: None,
                            "save": _save})
    monkeypatch.setitem(sys.modules, "edge_tts", mod)


def test_synthesize_creates_cached_file(tts_env, monkeypatch):
    _install_fake_edge_tts(
        monkeypatch,
        lambda path: open(path, "wb").write(b"\x00" * 100))
    name = tts.synthesize("下午两点高等数学课")
    assert name.endswith(".mp3")
    assert os.path.isfile(os.path.join(tts.cache_dir(), name))
    assert len(name) == 40 + len(".mp3")


def test_synthesize_cache_hit_no_resynthesis(tts_env, monkeypatch):
    _install_fake_edge_tts(
        monkeypatch,
        lambda path: open(path, "wb").write(b"\x00" * 100))
    first = tts.synthesize("同样文本")
    calls = []
    _install_fake_edge_tts(
        monkeypatch,
        lambda path: calls.append(path) or open(path, "wb").write(b"\x00"))
    second = tts.synthesize("同样文本")
    assert second == first
    assert calls == []


def test_synthesize_failure_returns_empty(tts_env, monkeypatch):
    def _boom(path):
        raise RuntimeError("network down")

    _install_fake_edge_tts(monkeypatch, _boom)
    assert tts.synthesize("失败文本") == ""
    assert os.listdir(tts.cache_dir()) == []


def test_audio_url(tts_env):
    assert tts.audio_url("abc.mp3") == \
        "http://192.168.0.9:5000/audio/tts/abc.mp3"


def test_synthesize_announcement_joins_with_gap(tts_env, monkeypatch):
    _install_fake_edge_tts(
        monkeypatch,
        lambda path: open(path, "wb").write(b"\x00" * 6000))
    name, dur = tts.synthesize_announcement("叮叮叮叮！上课提醒！", "第一节高数")
    assert name.endswith(".mp3")
    # 6000B 提示语 + 167 帧×144B 静音 + 6000B 正文 = 36048B → 6.008s
    assert abs(dur - 36048 / 6000) < 1e-6
    joined = os.path.join(tts.cache_dir(), name)
    with open(joined, "rb") as f:
        data = f.read()
    assert len(data) == 36048
    assert data[6000:6000 + 4] == bytes([0xFF, 0xF3, 0x64, 0xC4])  # 静音帧头
    mtime = os.path.getmtime(joined)
    name2, dur2 = tts.synthesize_announcement("叮叮叮叮！上课提醒！", "第一节高数")
    assert name2 == name
    assert os.path.getmtime(joined) == mtime


def test_synthesize_announcement_failure_returns_empty(tts_env, monkeypatch):
    def _boom(path):
        raise RuntimeError("network down")

    _install_fake_edge_tts(monkeypatch, _boom)
    name, dur = tts.synthesize_announcement("提示语", "正文")
    assert name == ""
    assert dur == 0.0

def test_duration_of_scales_with_size(tts_env):
    d = tts.cache_dir()
    with open(os.path.join(d, "a.mp3"), "wb") as f:
        f.write(b"\x00" * 30000)
    assert abs(tts.duration_of("a.mp3") - 5.0) < 1e-6
    assert tts.duration_of("missing.mp3") == 0.0

def test_silence_file_stable_and_kept_by_clear_cache(tts_env):
    name = tts.silence_file()
    assert name == tts.SILENCE_FILENAME
    path = os.path.join(tts.cache_dir(), name)
    assert os.path.getsize(path) == 1250 * 144  # 30s * 24ms/帧 * 144B
    mtime = os.path.getmtime(path)
    assert tts.silence_file() == name
    assert os.path.getmtime(path) == mtime
    assert tts.silence_url() == "http://192.168.0.9:5000/audio/tts/" + name
    for i in range(3):
        with open(os.path.join(tts.cache_dir(), f"x{i}.mp3"), "wb") as f:
            f.write(b"\x00" * 10)
    tts.clear_cache(max_files=1)
    assert os.path.isfile(path)  # 静音垫底文件不被淘汰