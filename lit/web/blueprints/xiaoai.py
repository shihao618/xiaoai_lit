"""小爱音箱蓝图：/api/xiaoai/* 9 条路由（云端原生播报 + 登录/设备管理）。"""
import logging

from flask import Blueprint, jsonify, request

from lit.xiaoai import diagnostics
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

        result = xiaoai_speaker.select_device(device_id)
        resp = {"success": True, "message": "设备已选择", "device": result}
        if result.get("warning"):
            resp["warning"] = result["warning"]
        return jsonify(resp)

    @bp.route("/status")
    def status():
        return jsonify(xiaoai_speaker.get_status())

    @bp.route("/test", methods=["POST"])
    def test():
        data = request.json or {}
        text = (data.get("text") or "").strip()
        if not text:
            return jsonify({"success": False, "message": "text 不能为空"})
        ok, message, detail = xiaoai_speaker.speak_with_detail(text)
        return jsonify({
            "success": ok,
            "message": message,
            "grade": detail.get("grade"),
            "grade_label": detail.get("grade_label"),
            "channel": detail.get("channel"),
            "warnings": detail.get("warnings") or [],
            "device": detail.get("device") or {},
            "observation": detail.get("observation") or {},
        })

    @bp.route("/stop_playback", methods=["POST"])
    def stop_playback():
        ok, message = xiaoai_speaker.stop_playback()
        return jsonify({"success": ok, "message": message})

    @bp.route("/broadcasts")
    def broadcasts():
        records = diagnostics.get_broadcasts()
        return jsonify({
            "success": True,
            "count": len(records),
            "broadcasts": records,
        })

    return bp