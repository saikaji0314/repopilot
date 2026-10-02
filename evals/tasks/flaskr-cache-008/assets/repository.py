"""工单的 SQLite 读写，只负责数据库操作。"""
from flaskr.db import get_db


def find_tickets(status=None):
    sql = "SELECT id, title, status FROM ticket"
    params = []
    if status is not None:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY id"
    return [dict(row) for row in get_db().execute(sql, params).fetchall()]


def get_ticket(ticket_id):
    row = get_db().execute(
        "SELECT id, title, status FROM ticket WHERE id = ?", (ticket_id,)
    ).fetchone()
    return dict(row) if row else None


def update_title(ticket_id, title):
    db = get_db()
    db.execute("UPDATE ticket SET title = ? WHERE id = ?", (title, ticket_id))
    db.commit()
    return get_ticket(ticket_id)
