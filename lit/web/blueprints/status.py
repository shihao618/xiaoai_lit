"""综合状态蓝图：/api/status（契约冻结自 app.py）。"""
from flask import Blueprint, jsonify


def create_status_blueprint(jwxt_client, xiaoai_speaker):
    bp = Blueprint("status", __name__, url_prefix="/api")

    @bp.route("/status")
    def overall_status():
        jwxt_ok = jwxt_client.cookie_valid
        xiaoai_ok = xiaoai_speaker.cookie_valid
        locked, lock_time = jwxt_client.is_locked()
        return jsonify({
            "jwxt": {
                "cookie_valid": jwxt_ok,
                "logged_in": jwxt_client.logged_in,
                "login_time": jwxt_client.login_time.isoformat() if jwxt_client.login_time else None,
                "account": jwxt_client.account,
                "locked": locked,
                "lock_time": lock_time.isoformat() if lock_time else None,
                "failed_attempts": jwxt_client.failed_attempts,
            },
            "xiaoai": {
                "cookie_valid": xiaoai_ok,
                "logged_in": xiaoai_speaker.logged_in,
                "login_time": xiaoai_speaker.login_time.isoformat() if xiaoai_speaker.login_time else None,
                "username": xiaoai_speaker.username,
                "device_id": xiaoai_speaker.device_id,
            },
        })


    return bp