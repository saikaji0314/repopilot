"""Session cookie 路径的配置优先级，设置和删除行为必须一致。"""
from http.cookies import SimpleCookie
from pathlib import Path

import flask
import pytest


def test_uses_workspace_source():
    expected = Path(__file__).resolve().parents[1] / "src" / "flask"
    assert Path(flask.__file__).resolve().parent == expected


@pytest.mark.parametrize(("root", "explicit", "expected"), [
    ("/portal", None, "/portal"),
    ("/portal", "/custom", "/custom"),
    ("/", None, "/"),
])
def test_cookie_path_precedence_for_set_and_clear(app, root, explicit, expected):
    app.config.update(APPLICATION_ROOT=root, SESSION_COOKIE_PATH=explicit)

    @app.route("/set")
    def set_value():
        flask.session["value"] = "example"
        return "set"

    @app.route("/clear")
    def clear_value():
        # 独立触发删除 cookie，不依赖测试客户端是否回送路径限定的 cookie。
        flask.session["value"] = "example"
        flask.session.clear()
        return "cleared"

    client = app.test_client(use_cookies=False)
    for endpoint in ("/set", "/clear"):
        response = client.get(endpoint)
        assert response.status_code == 200
        cookie = SimpleCookie()
        cookie.load(response.headers["Set-Cookie"])
        assert cookie[app.config["SESSION_COOKIE_NAME"]]["path"] == expected
