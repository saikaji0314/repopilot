# 名称/路径检索排序
from repopilot.legacy.retrieval.schema import RepositoryIndex, SearchHit, HitReason


def search_index(index: RepositoryIndex, query: str, limit=50) -> list[SearchHit]:
    if not query or limit <= 0:
        return []
    
    equal_symbols = []
    partial_symbols = []

    for symbol in index.symbols:
        if query == symbol.qualified_name.split(".")[-1]:
            target = equal_symbols
            reason = HitReason.EQUAL
        elif query in symbol.qualified_name.split(".")[-1]:
            target = partial_symbols
            reason = HitReason.PARTIAL
        else:
            continue
        target.append(SearchHit(
            path=symbol.path,
            start_line=symbol.start_line,
            end_line=symbol.end_line,
            reason=reason
        ))

    matched_paths = {hit.path for hit in equal_symbols + partial_symbols}
    path_hits = [
        SearchHit(path=path, reason=HitReason.PATH)
        for path in index.files
        if query in path and path not in matched_paths
    ]

    equal_symbols.sort(key=lambda x: (x.path, x.start_line))
    partial_symbols.sort(key=lambda x: (x.path, x.start_line))
    path_hits.sort(key=lambda x: x.path)

    return (equal_symbols + partial_symbols + path_hits)[:limit]
