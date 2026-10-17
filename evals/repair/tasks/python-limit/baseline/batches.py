"""输入已按从新到旧排列；limit只约束返回条数。"""


def newest(rows, limit=None):
    values = list(rows)
    if limit is None:
        return values
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError('limit must be a positive integer')
    return values[-limit:]
