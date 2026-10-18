# 去重、片段选择、字符预算
from pathlib import Path
from repopilot.legacy.retrieval.search import SearchHit
from repopilot.legacy.retrieval.schema import HitReason


def merge_overlapping_hits(hits: list[SearchHit]) -> list[SearchHit]:
    # 合并会调整范围和原因，复制一份，避免修改调用方的原始命中。
    ranged = [h.model_copy() for h in hits if h.start_line is not None]
    file_hits = [h for h in hits if h.start_line is None]

    ranged.sort(key=lambda h: (h.path, h.start_line))
    merged = []

    for hit in ranged:
        if not merged:
            merged.append(hit)
            continue
        last_hit = merged[-1]
        if hit.path == last_hit.path and hit.start_line <= last_hit.end_line:
            merged[-1].end_line = max(hit.end_line, last_hit.end_line)
            if hit.reason == HitReason.PARTIAL and last_hit.reason == HitReason.PARTIAL:
                merged[-1].reason = HitReason.PARTIAL
            else:
                merged[-1].reason = HitReason.EQUAL
        else:
            merged.append(hit)

    # 路径/行号排序只用于找重叠范围；返回前恢复精确匹配优先级。
    priority = {HitReason.EQUAL: 0, HitReason.PARTIAL: 1, HitReason.PATH: 2}
    return sorted(
        merged + file_hits,
        key=lambda h: (priority[h.reason], h.path, h.start_line or 0),
    )


def build_context(workspace_path: Path, hits: list[SearchHit], max_chars: int = 200) -> tuple[str, bool]:
    file_contents = {}
    context = ""
    truncate_str = "已截断"
    max_chars -= len(truncate_str)
    truncated = False

    for hit in hits:
        if hit.path not in file_contents:
            file_contents[hit.path] = (workspace_path / hit.path).read_text(encoding="utf-8")
        lines = file_contents[hit.path].splitlines()

        if hit.start_line is None:
            lines = lines[:20]
            append_str = f"{hit.path}:相关文件1-20预览\n"
        else:
            lines = lines[hit.start_line - 1: hit.end_line]
            append_str = f"{hit.path}:{hit.start_line}-{hit.end_line}\n"
        if len(context) + len(append_str) > max_chars:
            context += truncate_str
            truncated = True
            break
        context += append_str

        for i, line in enumerate(lines):
            append_str = f"{i + (hit.start_line if hit.start_line is not None else 1)}: {line}" + "\n"
            if len(context) + len(append_str) > max_chars:
                context += truncate_str
                truncated = True
                break
            context += append_str

        # 内层已检查并添加每一行；这里只在实际截断时停止。
        if truncated:
            break

    return context, truncated
