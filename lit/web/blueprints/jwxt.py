"""教务系统蓝图：/api/jwxt/* 5 条路由（契约冻结自 app.py）。"""
import base64
import logging

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)


def create_jwxt_blueprint(jwxt_client):
    bp = Blueprint("jwxt", __name__, url_prefix="/api/jwxt")

    @bp.route("/captcha")
    def captcha():
        try:
            image_data = jwxt_client.get_captcha_image()
            b64 = base64.b64encode(image_data).decode("utf-8")
            code = ""
            try:
                code = jwxt_client.recognize_captcha(image_data)
            except Exception as e:
                logger.warning(f"验证码OCR识别失败: {e}")
            return jsonify({"success": True, "image": b64, "code": code})
        except Exception as e:
            return jsonify({"success": False, "message": str(e)})

    @bp.route("/login", methods=["POST"])
    def login():
        data = request.json or {}
        account = data.get("account")
        password = data.get("password")
        verifycode = data.get("verifycode")

        if account:
            jwxt_client.account = account
        if password:
            jwxt_client.password = password

        result = jwxt_client.login(verifycode=verifycode)
        return jsonify(result)

    @bp.route("/status")
    def status():
        return jsonify(jwxt_client.get_status())

    @bp.route("/manual_cookie", methods=["POST"])
    def manual_cookie():
        data = request.json or {}
        cookie_str = data.get("cookie", "").strip()
        account = data.get("account", "").strip()
        if not cookie_str:
            return jsonify({"success": False, "message": "Cookie不能为空"})
        try:
            jwxt_client.set_manual_cookie(cookie_str, account or None)
            return jsonify({"success": True, "message": "Cookie设置成功"})
        except Exception as e:
            logger.error(f"手动设置Cookie失败: {e}")
            return jsonify({"success": False, "message": f"设置失败: {e}"})

    @bp.route("/courses")
    def courses():
        course_list = jwxt_client.get_course_table()
        if course_list:
            return jsonify({"success": True, "courses": course_list})
        return jsonify({"success": False, "message": "获取课表失败"})

    return bp