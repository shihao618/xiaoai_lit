"""本地 TTS 合成（edge-tts）+ 文本哈希缓存，供 player_play_music 通道播放。

L05B/L05C 等型号的 mibrain text_to_speech 被服务端静默忽略（返回 code=0
但不播报），这些型号必须走 player_play_music API 播放音频 URL，因此
播报文本需先合成为音频文件，由音箱通过局域网拉流播放。
"""

import asyncio
import hashlib
import logging
import os
import time

from lit import config

logger = logging.getLogger(__name__)

_CACHE_SUBDIR = "tts_cache"
_TTS_BYTES_PER_SEC = 6000  # edge-tts 固定 48kbps CBR mono mp3

SILENCE_FILENAME = "silence30.mp3"
_SILENCE_SECONDS = 30
# MPEG-2 Layer III 24kHz 48kbps mono 静音帧（与 edge-tts 输出同参数，
# 144 字节/帧、24ms/帧），用于 play_music 列表垫底，保证正文只播一遍
_SILENCE_FRAME = bytes([0xFF, 0xF3, 0x64, 0xC4]) + b"\x00" * 140
_SILENCE_FRAME_MS = 24


def cache_dir() -> str:
    path = os.path.join(config.CACHE_DIR, "xiaoai", _CACHE_SUBDIR)
    os.makedirs(path, exist_ok=True)
    return path


def duration_of(filename: str) -> float:
    """估算音频时长（秒）：edge-tts 输出为 48kbps CBR，时长=大小/速率。

    读取失败返回 0，由调用方用文本长度估算兜底。
    """
    try:
        size = os.path.getsize(os.path.join(cache_dir(), filename))
    except OSError:
        return 0.0
    return size / _TTS_BYTES_PER_SEC


def silence_file() -> str:
    """确保静音垫底音频存在，返回文件名。

    play_music 列表在 L05B/L05C 上会无限循环且 stop 指令被固件忽略，
    列表第二项放静音使 pause 落在静音段，正文恰好播放一遍。
    """
    path = os.path.join(cache_dir(), SILENCE_FILENAME)
    expected = int(_SILENCE_SECONDS * 1000 / _SILENCE_FRAME_MS) * len(_SILENCE_FRAME)
    if not os.path.isfile(path) or os.path.getsize(path) != expected:
        with open(path, "wb") as f:
            f.write(_SILENCE_FRAME * int(_SILENCE_SECONDS * 1000 / _SILENCE_FRAME_MS))
    return SILENCE_FILENAME


def silence_url() -> str:
    return audio_url(silence_file())


def audio_url(filename: str) -> str:
    base = (getattr(config, "XIAOAI_TTS_BASE_URL", "") or "").rstrip("/")
    return base + "/audio/tts/" + filename


def base_url_warnings() -> list:
    """XIAOAI_TTS_BASE_URL 配置告警（空值 / 回环误配，启动与播报双点消费）。"""
    warnings = []
    base = (getattr(config, "XIAOAI_TTS_BASE_URL", "") or "").strip()
    if not base:
        warnings.append(
            "XIAOAI_TTS_BASE_URL 未配置：本地TTS主通道未启用，播报依赖回退通道，"
            "L05B/L05C 等型号会无声（提示成功但无声）")
        return warnings
    if "localhost" in base or "127.0.0.1" in base:
        warnings.append(
            "XIAOAI_TTS_BASE_URL 配置为 {}：音箱经局域网拉流无法访问本机回环地址，"
            "应使用宿主机局域网地址（如 http://192.168.x.x:10379）".format(base))
    return warnings


def log_base_url_warnings() -> None:
    for warning in base_url_warnings():
        logger.warning("%s", warning)


def _file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _remove_quietly(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def _min_expected_bytes(text: str) -> int:
    """合成产物大小下限（R9 误伤防线）：仅拦截空/截断/垃圾产物。

    2400B = 48kbps CBR 下 0.4 秒下限；300B/字符 ≈ 正常 1200B/字符的
    4 倍宽容线。
    """
    return max(2400, 300 * len(text))


_consecutive_failures = 0


def synthesize_announcement(prompt: str, text: str, gap_seconds: float = 4.0,
                            voice: str = None) -> tuple:
    """合成"提示语 + 静音间隔 + 正文"的单文件播报音频。

    三段均为同参数 CBR mp3（帧结构一致），字节级拼接无缝。
    返回 (文件名, 时长秒, 耗时秒)——耗时为本次调用总耗时（含两段合成
    或缓存命中，缓存命中近零）；任一段合成失败返回 ("", 0.0, 耗时秒)。
    """
    global _consecutive_failures
    from . import constants

    started = time.monotonic()
    voice = voice or constants.TTS_VOICE
    prompt_name = synthesize(prompt, voice)
    body_name = synthesize(text, voice)
    if not prompt_name or not body_name:
        _consecutive_failures += 1
        if _consecutive_failures >= 3:
            logger.error("edge-tts 服务疑似劣化，连续 %d 次合成失败",
                         _consecutive_failures)
        return "", 0.0, time.monotonic() - started
    _consecutive_failures = 0
    gap_frames = round(gap_seconds * 1000 / _SILENCE_FRAME_MS)
    digest = hashlib.sha1(
        (voice + "|join|" + prompt + "|" + str(gap_seconds) + "|" + text)
        .encode("utf-8")).hexdigest()
    filename = digest + ".mp3"
    path = os.path.join(cache_dir(), filename)
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        with open(os.path.join(cache_dir(), prompt_name), "rb") as f:
            prompt_bytes = f.read()
        with open(os.path.join(cache_dir(), body_name), "rb") as f:
            body_bytes = f.read()
        with open(path, "wb") as f:
            f.write(prompt_bytes)
            f.write(_SILENCE_FRAME * gap_frames)
            f.write(body_bytes)
    elapsed = time.monotonic() - started
    if elapsed > 10:
        logger.warning("edge-tts 合成耗时 %.1fs（劣化历史区间 23-44s），建议关注",
                       elapsed)
    return filename, duration_of(filename), elapsed


def synthesize(text: str, voice: str = None) -> str:
    """合成语音并返回文件名；失败返回空字符串。

    同文本（含音色）命中缓存且通过大小校验时不重复合成；预置异常
    小文件视为未命中并清理后重新合成（缓存污染清理规则）。
    一次调用至多一次合成尝试，失败不重试（零重试定案，回退交由
    调用方处理）。文件名不泄漏文本内容。
    """
    from . import constants

    voice = voice or constants.TTS_VOICE
    digest = hashlib.sha1((voice + "|" + text).encode("utf-8")).hexdigest()
    filename = digest + ".mp3"
    path = os.path.join(cache_dir(), filename)
    min_bytes = _min_expected_bytes(text)
    if os.path.isfile(path):
        if _file_size(path) >= min_bytes:
            return filename
        logger.warning("TTS缓存命中但校验不过（%d 字节 < 预期下限 %d），"
                       "视为未命中并清理", _file_size(path), min_bytes)
        _remove_quietly(path)

    import edge_tts

    async def _run():
        await edge_tts.Communicate(text, voice).save(path + ".tmp")

    started = time.monotonic()
    try:
        asyncio.run(asyncio.wait_for(_run(), timeout=constants.TTS_SYNTH_TIMEOUT))
        os.replace(path + ".tmp", path)
        size = _file_size(path)
        if size < min_bytes:
            logger.warning("本地TTS产物校验不过（%d 字节 < 预期下限 %d，"
                           "耗时 %.1fs），按失败处理",
                           size, min_bytes, time.monotonic() - started)
            _remove_quietly(path)
            _remove_quietly(path + ".tmp")
            return ""
        return filename
    except Exception as e:
        logger.warning("本地TTS合成失败: %s", e)
        for p in (path, path + ".tmp"):
            _remove_quietly(p)
        return ""


def clear_cache(max_files: int = 200) -> None:
    """按修改时间淘汰最旧的合成文件，控制磁盘占用（静音垫底文件除外）。"""
    path = cache_dir()
    try:
        items = [
            os.path.join(path, f) for f in os.listdir(path)
            if f.endswith(".mp3") and f != SILENCE_FILENAME
        ]
    except OSError:
        return
    if len(items) <= max_files:
        return
    items.sort(key=lambda p: os.path.getmtime(p))
    for old in items[: len(items) - max_files]:
        try:
            os.remove(old)
        except OSError:
            pass

def purge_cache() -> tuple:
    """每日全量清理合成缓存：删除目录内全部 *.mp3（豁免 silence30.mp3）。

    返回 (删除文件数, 释放字节数)；目录不存在返回 (0, 0)；
    单文件删除失败计数汇总记日志，不中断其余文件。
    """
    path = cache_dir()
    count = 0
    size = 0
    failed = 0
    try:
        names = [n for n in os.listdir(path) if n.endswith(".mp3") and n != SILENCE_FILENAME]
    except OSError:
        return 0, 0
    for name in names:
        p = os.path.join(path, name)
        try:
            s = os.path.getsize(p)
            os.remove(p)
        except OSError:
            failed += 1
            continue
        count += 1
        size += s
    if failed:
        logger.warning("TTS缓存清理有 %d 个文件删除失败", failed)
    return count, size