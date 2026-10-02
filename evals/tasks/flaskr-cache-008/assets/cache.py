"""每个 Flask app 独立的本地详情缓存；不依赖 Redis。"""
from flask import current_app


def ticket_key(ticket_id):
    return f"ticket:{ticket_id}"


def _store():
    return current_app.extensions.setdefault("ticket_cache", {})


def read(key):
    value = _store().get(key)
    return dict(value) if value is not None else None


def write(key, value):
    _store()[key] = dict(value)


def invalidate(key):
    _store().pop(key, None)
