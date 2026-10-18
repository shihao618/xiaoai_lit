"""页面蓝图：GET /（index.html 渲染）。"""
from flask import Blueprint, render_template


def create_pages_blueprint():
    bp = Blueprint("pages", __name__)

    @bp.route("/")
    def index():
        return render_template("index.html")

    return bp