import os
import logging
import base64
from flask import Flask, render_template, jsonify, request, send_from_directory
from lit import config
from lit.jwxt import JWXTClient
from lit.tasks import BackgroundTasks
from lit.xiaoai import XiaoAISpeaker
from lit.xiaoai.exceptions import QRSessionError

logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder="templates", static_folder="static")

jwxt_client = JWXTClient()
xiaoai_speaker = XiaoAISpeaker()
tasks = BackgroundTasks(jwxt_client, xiaoai_speaker)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/jwxt/captcha")
def jwxt_captcha():
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


@app.route("/api/jwxt/login", methods=["POST"])
def jwxt_login():
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


@app.route("/api/jwxt/status")
def jwxt_status():
    return jsonify(jwxt_client.get_status())


@app.route("/api/jwxt/manual_cookie", methods=["POST"])
def jwxt_manual_cookie():
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


@app.route("/api/jwxt/courses")
def jwxt_courses():
    courses = jwxt_client.get_course_table()
    if courses:
        return jsonify({"success": True, "courses": courses})
    return jsonify({"success": False, "message": "获取课表失败"})


@app.route("/api/xiaoai/qr/start", methods=["POST"])
def xiaoai_qr_start():
    result = xiaoai_speaker.start_qr_login()
    return jsonify(result)


@app.route("/api/xiaoai/qr/poll")
def xiaoai_qr_poll():
    session_id = (request.args.get("session_id") or "").strip()
    try:
        result = xiaoai_speaker.get_qr_status(session_id)
        return jsonify(result)
    except QRSessionError:
        return jsonify({"success": False, "message": "登录会话不存在或已结束"}), 404


@app.route("/api/xiaoai/login_status")
def xiaoai_login_status():
    state = xiaoai_speaker.get_login_state()
    return jsonify({
        "status": state["status"],
        "message": state["message"],
        "devices": state["devices"],
        "otp_method": None,
    })


@app.route("/audio/tts/<path:filename>")
def tts_audio(filename):
    """本地 TTS 合成音频服务（音箱通过局域网拉流播放）。"""
    from lit.xiaoai import tts as tts_module

    if not filename.endswith(".mp3") or "/" in filename or "\\" in filename:
        return jsonify({"success": False, "message": "非法文件名"}), 404
    return send_from_directory(tts_module.cache_dir(), filename)


@app.route("/api/xiaoai/manual_token", methods=["POST"])
def xiaoai_manual_token():
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


@app.route("/api/xiaoai/manual_cookie", methods=["POST"])
def xiaoai_manual_cookie():
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


@app.route("/api/xiaoai/devices")
def xiaoai_devices():
    try:
        devices = xiaoai_speaker.get_devices()
        return jsonify({"success": True, "devices": devices})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/xiaoai/select_device", methods=["POST"])
def xiaoai_select_device():
    data = request.json or {}
    device_id = data.get("device_id")
    if not device_id:
        return jsonify({"success": False, "message": "设备ID为空"})

    xiaoai_speaker.select_device(device_id)
    return jsonify({"success": True, "message": "设备已选择"})


@app.route("/api/xiaoai/status")
def xiaoai_status():
    return jsonify(xiaoai_speaker.get_status())


@app.route("/api/xiaoai/test", methods=["POST"])
def xiaoai_test():
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


@app.route("/api/status")
def overall_status():
    jwxt_ok = jwxt_client.check_cookie_valid()
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


@app.route("/api/course_time_map")
def course_time_map():
    return jsonify(config.COURSE_TIME_MAP)


def start_background_tasks():
    try:
        tasks.start()
    except Exception as e:
        logger.error(f"后台任务启动失败: {e}")


def create_app():
    os.makedirs(config.DATA_DIR, exist_ok=True)
    os.makedirs(config.CACHE_DIR, exist_ok=True)
    xiaoai_speaker.restore_on_start()
    start_background_tasks()
    return app


if __name__ == "__main__":
    logging.basicConfig(level=config.LOG_LEVEL, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    create_app()
    app.run(host="0.0.0.0", port=5000, debug=False)