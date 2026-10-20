"""页面模板结构测试：卡片必须都在 .container 内、div 标签配平（防 UI 错位回归）。"""
import os
from html.parser import HTMLParser

from flask import Flask

from lit.web.blueprints.pages import create_pages_blueprint

TEMPLATES = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "lit", "web", "templates"))


class _DivStructure(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []          # 当前 div 祖先的 class 列表
        self.closed_ok = True
        self.markers = {}        # 文本 -> 出现时所在祖先 class 链

    def handle_starttag(self, tag, attrs):
        if tag != "div":
            return
        classes = (dict(attrs).get("class") or "").split()
        self.stack.append(classes)

    def handle_endtag(self, tag):
        if tag != "div":
            return
        if not self.stack:
            self.closed_ok = False   # 多余的 </div>
            return
        self.stack.pop()

    def handle_data(self, data):
        for marker in ("播报测试", "播报诊断", "课表预览"):
            if marker in data and marker not in self.markers:
                flat = [c for frame in self.stack for c in frame]
                self.markers[marker] = (flat, len(self.stack))


def _render_index() -> str:
    app = Flask(__name__, template_folder=TEMPLATES, static_folder=None)
    app.register_blueprint(create_pages_blueprint())
    response = app.test_client().get("/")
    assert response.status_code == 200
    return response.get_data(as_text=True)


def test_index_div_tags_balanced():
    parser = _DivStructure()
    parser.feed(_render_index())
    parser.close()
    assert parser.closed_ok is True
    assert parser.stack == []          # 所有 div 正常闭合


def test_cards_are_inside_container():
    """卡片（含播报诊断）必须嵌套在 .container 内，否则会被渲染成全宽错位。"""
    parser = _DivStructure()
    parser.feed(_render_index())
    parser.close()
    for marker in ("课表预览", "播报测试", "播报诊断"):
        assert marker in parser.markers, f"未找到卡片：{marker}"
        classes, depth = parser.markers[marker]
        assert "container" in classes, f"{marker} 不在 .container 内"
        assert "card" in classes, f"{marker} 不在 .card 内"
        assert depth >= 2, f"{marker} div 层级异常（depth={depth}）"


def test_index_has_no_stray_closing_div_before_diagnostics():
    html = _render_index()
    # 播报诊断卡片必须紧跟在 .container 内，且其前面不能提前出现容器闭合
    idx = html.index("播报诊断")
    before = html[:idx]
    # 统计 container 之后 card 数量应 ≥ 4（教务/小爱/课表/播报测试）
    assert before.count('class="card"') >= 4
