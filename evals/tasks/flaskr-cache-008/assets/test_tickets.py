"""工单模块的接口契约测试；每个用例使用独立应用与 SQLite 数据库。"""
import pytest
from flask import Flask
from flaskr.db import get_db
from flaskr.tickets import bp
from flaskr.tickets import cache


@pytest.fixture
def ticket_app(app):
    app.register_blueprint(bp)
    with app.app_context():
        db = get_db()
        db.executescript("""
            CREATE TABLE ticket (
                id INTEGER PRIMARY KEY, title TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('open', 'closed'))
            );
            INSERT INTO ticket VALUES (1, 'First ticket', 'open');
            INSERT INTO ticket VALUES (2, 'Second ticket', 'closed');
            INSERT INTO ticket VALUES (3, 'Third ticket', 'open');
        """)
        db.commit()
    return app


@pytest.fixture
def ticket_client(ticket_app):
    return ticket_app.test_client()


class TestTicketFiltering:
    @pytest.mark.parametrize(("status", "expected_ids"), [("open", [1, 3]), ("closed", [2])])
    def test_status_filter(self, ticket_client, status, expected_ids):
        response = ticket_client.get("/tickets", query_string={"status": status})
        assert response.status_code == 200
        assert [item["id"] for item in response.json["items"]] == expected_ids
        assert all(item["status"] == status for item in response.json["items"])

    def test_without_filter(self, ticket_client):
        response = ticket_client.get("/tickets")
        assert response.status_code == 200
        assert [item["id"] for item in response.json["items"]] == [1, 2, 3]

    def test_invalid_filter(self, ticket_client):
        assert ticket_client.get("/tickets?status=unknown").status_code == 400


class TestTicketCache:
    def test_warm_read_after_update(self, ticket_app, ticket_client):
        # 先缓存两条，再修改第一条；不能通过清空全部缓存规避。
        assert ticket_client.get("/tickets/1").json["title"] == "First ticket"
        second = ticket_client.get("/tickets/2").json
        response = ticket_client.patch("/tickets/1", json={"title": "Renamed"})
        assert response.status_code == 200
        assert response.json["title"] == "Renamed"
        with ticket_app.app_context():
            stored = get_db().execute("SELECT title FROM ticket WHERE id = 1").fetchone()
            assert stored["title"] == "Renamed"
            assert cache.read(cache.ticket_key(2)) == second
        assert ticket_client.get("/tickets/1").json["title"] == "Renamed"
        assert ticket_client.get("/tickets/2").json == second

    def test_cold_read_after_update(self, ticket_client):
        response = ticket_client.patch("/tickets/1", json={"title": "Cold rename"})
        assert response.status_code == 200
        assert ticket_client.get("/tickets/1").json["title"] == "Cold rename"

    def test_cache_is_app_local(self):
        # 不依赖执行顺序，两个 app 的相同缓存键也不能共享内容。
        first, second = Flask("first"), Flask("second")
        with first.app_context():
            cache.write(cache.ticket_key(1), {"title": "Only first"})
        with second.app_context():
            assert cache.read(cache.ticket_key(1)) is None
