from flaskr.db import get_db


def test_index_shows_newest_post_first(app, client):
    with app.app_context():
        db = get_db()
        db.execute(
            "INSERT INTO post (title, body, author_id, created) VALUES (?, ?, ?, ?)",
            ("newest title", "newest body", 1, "2020-01-01 00:00:00"),
        )
        db.commit()

    response = client.get("/")

    assert response.data.index(b"newest title") < response.data.index(b"test title")
