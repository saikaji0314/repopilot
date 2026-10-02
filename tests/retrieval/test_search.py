"""第 2 期步骤 3：用文件和符号索引检索，不调用模型。"""

from repopilot.retrieval.schema import (
    HitReason,
    RepositoryIndex,
    Symbol,
    SymbolKind,
)
from repopilot.retrieval.search import search_index


def make_index(symbols: list[Symbol], files: list[str]) -> RepositoryIndex:
    """构造最小索引，让测试只关注命中与排序。"""
    return RepositoryIndex(symbols=symbols, files=files, warnings=[], truncated=False)


def symbol(path: str, name: str, line: int) -> Symbol:
    return Symbol(
        path=path,
        qualified_name=name,
        kind=SymbolKind.FUNCTION,
        start_line=line,
        end_line=line + 2,
    )


def test_search_index_puts_exact_symbol_before_partial_and_path():
    """精确符号优先，之后是名称部分命中，最后才是文件路径。"""
    index = make_index(
        symbols=[
            symbol("z.py", "update", 10),
            symbol("a.py", "Service.update_extra", 4),
        ],
        files=["z.py", "a.py", "update_helpers.py"],
    )

    hits = search_index(index, "update", limit=10)

    assert [(h.path, h.start_line, h.reason) for h in hits] == [
        ("z.py", 10, HitReason.EQUAL),
        ("a.py", 4, HitReason.PARTIAL),
        ("update_helpers.py", None, HitReason.PATH),
    ]


def test_search_index_treats_method_name_as_exact_match():
    """查询 update 时，Service.update 是方法名精确匹配。"""
    index = make_index(
        symbols=[symbol("service.py", "Service.update", 5)],
        files=["service.py"],
    )

    hits = search_index(index, "update", limit=10)

    assert len(hits) == 1
    assert hits[0].reason == HitReason.EQUAL
    assert (hits[0].start_line, hits[0].end_line) == (5, 7)


def test_search_index_sorts_ties_by_path_then_line():
    """同一优先级按路径、起始行排序，不依赖索引原本的排列。"""
    index = make_index(
        symbols=[
            symbol("z.py", "update", 9),
            symbol("a.py", "update", 12),
            symbol("a.py", "update", 3),
        ],
        files=["z.py", "a.py"],
    )

    hits = search_index(index, "update", limit=10)

    assert [(h.path, h.start_line) for h in hits] == [
        ("a.py", 3),
        ("a.py", 12),
        ("z.py", 9),
    ]


def test_search_index_returns_one_path_hit_for_file_without_symbol_match():
    """路径命中代表文件，不按文件中的函数数量重复返回。"""
    index = make_index(
        symbols=[
            symbol("blog.py", "create", 1),
            symbol("blog.py", "update", 20),
        ],
        files=["blog.py", "empty_blog.py"],
    )

    hits = search_index(index, "blog", limit=10)

    assert [(h.path, h.start_line, h.end_line, h.reason) for h in hits] == [
        ("blog.py", None, None, HitReason.PATH),
        ("empty_blog.py", None, None, HitReason.PATH),
    ]


def test_search_index_applies_limit_after_ranking():
    """限额保留优先级最高的结果，不能先截取原始索引。"""
    index = make_index(
        symbols=[symbol("a.py", "pre_update_hook", 1), symbol("z.py", "update", 5)],
        files=["update_notes.py", "a.py", "z.py"],
    )

    hits = search_index(index, "update", limit=1)

    assert [(h.path, h.reason) for h in hits] == [("z.py", HitReason.EQUAL)]


def test_search_index_empty_or_missing_query_returns_no_hits():
    """空查询、无匹配和零限额都返回空列表。"""
    index = make_index([symbol("app.py", "run", 1)], ["app.py"])

    assert search_index(index, "") == []
    assert search_index(index, "missing") == []
    assert search_index(index, "run", limit=0) == []
