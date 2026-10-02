"""业务层：列表查询、详情缓存及标题更新。"""
from . import cache, repository


def list_tickets(status=None):
    return repository.find_tickets(status=status)


def ticket_detail(ticket_id):
    key = cache.ticket_key(ticket_id)
    cached = cache.read(key)
    if cached is not None:
        return cached
    ticket = repository.get_ticket(ticket_id)
    if ticket is not None:
        cache.write(key, ticket)
    return ticket


def rename_ticket(ticket_id, title):
    if repository.get_ticket(ticket_id) is None:
        return None
    updated = repository.update_title(ticket_id, title)
    cache.invalidate(cache.ticket_key(ticket_id))
    return updated
