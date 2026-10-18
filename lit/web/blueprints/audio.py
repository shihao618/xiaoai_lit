"""音频服务蓝图：/audio/tts/<filename>（契约冻结自 app.py）。"""
from flask import Blueprint, jsonify, send_from_directory


def create_audio_blueprint():
    bp = Blueprint("audio", __name__, url_prefix="/audio")

    @bp.route("/tts/<path:filename>")
    def tts_audio(filename):
        """本地 TTS 合成音频服务（音箱通过局域网拉流播放）。"""
        from lit.xiaoai import tts as tts_module

        if not filename.endswith(".mp3") or "/" in filename or "\\" in filename:
            return jsonify({"success": False, "message": "非法文件名"}), 404
        return send_from_directory(tts_module.cache_dir(), filename)

    return bp