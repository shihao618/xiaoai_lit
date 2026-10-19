"""教务系统蓝图：/api/jwxt/* 6 条路由（契约冻结自 app.py + Q4 显式扩展 term_config）。"""
import base64
import logging
from datetime import date

from flask import Blueprint, jsonify, request

from lit.jwxt import term

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

    @bp.route("/term_config", methods=["POST"])
    def term_config():
        """Q4 手动兜底：设置第 1 教学周周一锚点（未登录亦可配置，绑定当前 xnxqdm）。"""
        data = request.json or {}
        raw = str(data.get("term_start_monday") or "").strip()
        if not raw:
            return jsonify({"success": False, "message": "锚点日期不能为空"})
        d = term.parse_anchor(raw)
        if d is None:
            try:
                date.fromisoformat(raw)
            except (ValueError, TypeError):
                return jsonify({"success": False, "message": "日期格式非法，应为 YYYY-MM-DD"})
            return jsonify({"success": False, "message": f"{raw} 不是周一，第 1 教学周锚点必须为周一"})
        try:
            term.save_anchor(d, jwxt_client.xnxqdm, "manual")
            return jsonify({"success": True, "message": f"学期锚点已设置: {d.isoformat()}"})
        except OSError as e:
            logger.error(f"保存学期锚点失败: {e}")
            return jsonify({"success": False, "message": f"设置失败: {e}"})

    return bp