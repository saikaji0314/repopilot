"""Blueprint URL 默认值应按路由与注册实例隔离。"""
from pathlib import Path

import flask


def test_uses_workspace_source():
    # 防止测试误用虚拟环境中安装的 Flask，而非当前缺陷副本。
    expected = Path(__file__).resolve().parents[1] / "src" / "flask"
    assert Path(flask.__file__).resolve().parent == expected


def make_blueprint():
    bp = flask.Blueprint("pages", __name__, url_defaults={"language": "en"})

    @bp.route("/special", defaults={"language": "fr"})
    def special(language):
        return language

    @bp.route("/normal")
    def normal(language):
        return language

    return bp


def test_route_override_does_not_leak(app, client):
    bp = make_blueprint()
    app.register_blueprint(bp, url_prefix="/one")
    assert client.get("/one/special").data == b"fr"
    assert client.get("/one/normal").data == b"en"
    assert bp.url_values_defaults == {"language": "en"}


def test_registration_defaults_remain_independent(app, client):
    bp = make_blueprint()
    app.register_blueprint(bp, url_prefix="/one", url_defaults={"language": "de"})
    app.register_blueprint(bp, name="pages_two", url_prefix="/two",
                           url_defaults={"language": "es"})
    assert client.get("/one/special").data == b"fr"
    assert client.get("/two/special").data == b"fr"
    assert client.get("/one/normal").data == b"de"
    assert client.get("/two/normal").data == b"es"
