"""启动迁移编排：旧平铺布局 → 按域分组布局（一次性，幂等，失败不阻断启动）。

五类迁移固定顺序：① 教务状态合并 ② 锁定文件 ③ 小爱凭证 ④ 课表缓存 ⑤ TTS 缓存目录。
目标路径全部消费各域模块的路径函数（本模块不硬编码目标布局），
跨挂载点搬移统一 shutil.move（data→cache 为独立 bind mount，os.rename 会抛 EXDEV）。
"""
import logging
import os
import shutil

from lit import config
from lit.jwxt import courses as jwxt_courses
from lit.jwxt import lock as jwxt_lock
from lit.jwxt import state as jwxt_state
from lit.xiaoai import credentials as xiaoai_credentials
from lit.xiaoai import tts as xiaoai_tts

logger = logging.getLogger(__name__)


def _move_file(src: str, dst_dir: str) -> None:
    """单文件搬移：源存在才动作；目标同名已存在跳过；失败记警告不抛出。"""
    if not os.path.exists(src):
        return
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, os.path.basename(src))
    if os.path.exists(dst):
        logger.warning("数据迁移跳过（目标已存在）: %s", dst)
        return
    try:
        shutil.move(src, dst)
        logger.info("数据迁移: %s → %s", src, dst)
    except Exception as e:
        logger.warning("数据迁移失败: %s → %s: %s", src, dst, e)


def _move_tts_cache() -> None:
    """tts_cache 整目录搬移：data/tts_cache → cache/xiaoai/tts_cache（逐文件跨卷）。"""
    src_dir = os.path.join(config.DATA_DIR, "tts_cache")
    if not os.path.isdir(src_dir):
        return
    dst_dir = xiaoai_tts.cache_dir()
    moved = skipped = failed = 0
    for name in os.listdir(src_dir):
        src = os.path.join(src_dir, name)
        if not os.path.isfile(src):
            continue
        dst = os.path.join(dst_dir, name)
        if os.path.exists(dst):
            skipped += 1
            continue
        try:
            shutil.move(src, dst)
            moved += 1
        except Exception as e:
            failed += 1
            logger.warning("TTS缓存迁移失败: %s → %s: %s", src, dst, e)
    try:
        os.rmdir(src_dir)
    except OSError:
        logger.warning("TTS缓存旧目录非空或被占用，保留待下次启动重试: %s", src_dir)
        return
    logger.info("TTS缓存迁移完成: 迁移 %d 个文件（跳过 %d，失败 %d）", moved, skipped, failed)


def run_startup_migration() -> None:
    """启动迁移编排入口：五步独立容错，任一步失败不阻断启动。"""
    try:
        jwxt_state.merge_legacy_state()
    except Exception as e:
        logger.warning("数据迁移失败: 教务状态合并: %s", e)
    try:
        _move_file(
            os.path.join(config.DATA_DIR, "jwxt_lock.json"),
            os.path.dirname(jwxt_lock._lock_file()),
        )
    except Exception as e:
        logger.warning("数据迁移失败: jwxt_lock.json: %s", e)
    try:
        _move_file(
            os.path.join(config.DATA_DIR, xiaoai_credentials.CREDENTIALS_FILENAME),
            os.path.dirname(xiaoai_credentials.CredentialStore().path),
        )
    except Exception as e:
        logger.warning("数据迁移失败: 小爱凭证: %s", e)
    try:
        _move_file(
            os.path.join(config.CACHE_DIR, "course_table.json"),
            os.path.dirname(jwxt_courses._cache_file()),
        )
    except Exception as e:
        logger.warning("数据迁移失败: 课表缓存: %s", e)
    try:
        _move_tts_cache()
    except Exception as e:
        logger.warning("数据迁移失败: TTS缓存目录: %s", e)