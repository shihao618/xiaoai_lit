"""小爱音箱蓝图：/api/xiaoai/* 9 条路由（契约冻结自 app.py）。"""
import logging

from flask import Blueprint, jsonify, request

from lit.xiaoai.exceptions import QRSessionError

logger = logging.getLogger(__name__)


def create_xiaoai_blueprint(xiaoai_speaker):
    bp = Blueprint("xiaoai", __name__, url_prefix="/api/xiaoai")

    @bp.route("/qr/start", methods=["POST"])
    def qr_start():
        result = xiaoai_speaker.start_qr_login()
        return jsonify(result)

    @bp.route("/qr/poll")
    def qr_poll():
        session_id = (request.args.get("session_id") or "").strip()
        try:
            result = xiaoai_speaker.get_qr_status(session_id)
            return jsonify(result)
        except QRSessionError:
            return jsonify({"success": False, "message": "登录会话不存在或已结束"}), 404

    @bp.route("/login_status")
    def login_status():
        state = xiaoai_speaker.get_login_state()
        return jsonify({
            "status": state["status"],
            "message": state["message"],
            "devices": state["devices"],
            "otp_method": None,
        })

    @bp.route("/manual_token", methods=["POST"])
    def manual_token():
        data = request.json or {}
        token_str = data.get("token", "").strip()
        username = data.get("username", "").strip()
        if not token_str:
            return jsonify({"success": False, "message": "Token不能为空"})
        try:
            xiaoai_speaker.set_manual_token(token_str, username or None)
            status = xiaoai_speaker.get_status()
            return jsonify({"success": True, "message": "Token设置成功",
                            "devices": status["devices"]})
        except Exception as e:
            logger.error(f"手动设置Token失败: {e}")
            return jsonify({"success": False, "message": str(e)})

    @bp.route("/manual_cookie", methods=["POST"])
    def manual_cookie():
        data = request.json or {}
        cookie_str = data.get("cookie", "").strip()
        username = data.get("username", "").strip()
        if not cookie_str:
            return jsonify({"success": False, "message": "Cookie不能为空"})
        try:
            xiaoai_speaker.set_manual_cookie(cookie_str, username or None)
            status = xiaoai_speaker.get_status()
            return jsonify({"success": True, "message": "Cookie设置成功",
                            "devices": status["devices"]})
        except Exception as e:
            logger.error(f"手动设置Cookie失败: {e}")
            return jsonify({"success": False, "message": str(e)})

    @bp.route("/devices")
    def devices():
        try:
            device_list = xiaoai_speaker.get_devices()
            return jsonify({"success": True, "devices": device_list})
        except Exception as e:
            return jsonify({"success": False, "message": str(e)})

    @bp.route("/select_device", methods=["POST"])
    def select_device():
        data = request.json or {}
        device_id = data.get("device_id")
        if not device_id:
            return jsonify({"success": False, "message": "设备ID为空"})

        xiaoai_speaker.select_device(device_id)
        return jsonify({"success": True, "message": "设备已选择"})

    @bp.route("/status")
    def status():
        return jsonify(xiaoai_speaker.get_status())

    @bp.route("/test", methods=["POST"])
    def test():
        data = request.json or {}
        text = (data.get("text") or "").strip()
        url = (data.get("url") or "").strip()
        if not text and not url:
            return jsonify({"success": False, "message": "text 与 url 至少一项非空"})
        if not text and url:
            ok, message = xiaoai_speaker.play_url_direct(url)
            return jsonify({"success": ok, "message": message})
        ok, message = xiaoai_speaker.speak_with_result(text, backup_url=url or None)
        return jsonify({"success": ok, "message": message})

    return bp