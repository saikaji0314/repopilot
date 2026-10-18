"""第 2 期步骤 4：范围去重、代码片段与字符预算。

约定：
- merge_overlapping_hits(hits) 返回合并结果；同文件重叠范围合并。
- build_context(workspace_path, hits, max_chars) 返回 (文本, 是否截断)。
  文本包含工作区相对路径和 ``行号: 内容``；发生预算截断时以 ``已截断`` 结尾。
- max_chars 限制返回文本的长度，包括来源、行号、换行和截断提示。
"""

from repopilot.legacy.retrieval import context
from repopilot.legacy.retrieval.schema import HitReason, SearchHit


def make_hit(
    path: str,
    start: int | None,
    end: int | None,
    reason: HitReason = HitReason.PARTIAL,
) -> SearchHit:
    """让每个测试只突出它关心的路径、范围和原因。"""
    return SearchHit(path=path, start_line=start, end_line=end, reason=reason)


def test_merge_overlapping_hits_merges_overlapping_ranges():
    """同文件的重叠范围合并成一条命中。"""
    hits = [make_hit("blog.py", 10, 20), make_hit("blog.py", 15, 25)]

    merged = context.merge_overlapping_hits(hits)

    assert [(h.path, h.start_line, h.end_line) for h in merged] == [
        ("blog.py", 10, 25)
    ]


def test_merge_overlapping_hits_keeps_different_files_separate():
    """行号相同但文件不同，不属于重复片段。"""
    hits = [make_hit("blog.py", 3, 8), make_hit("auth.py", 3, 8)]

    merged = context.merge_overlapping_hits(hits)

    assert {(h.path, h.start_line, h.end_line) for h in merged} == {
        ("blog.py", 3, 8),
        ("auth.py", 3, 8),
    }


def test_merge_overlapping_hits_merges_chain_and_keeps_best_reason():
    """A 与 B、B 与 C 相交时合成一段；精确命中的原因优先保留。"""
    hits = [
        make_hit("blog.py", 10, 14),
        make_hit("blog.py", 18, 22),
        make_hit("blog.py", 13, 19, HitReason.EQUAL),
    ]

    merged = context.merge_overlapping_hits(hits)

    assert len(merged) == 1
    assert (merged[0].path, merged[0].start_line, merged[0].end_line) == (
        "blog.py", 10, 22
    )
    assert merged[0].reason == HitReason.EQUAL


def test_merge_overlapping_hits_accepts_file_only_hit_and_empty_input():
    """路径命中没有行号，不应参与整数范围比较。"""
    file_hit = make_hit("blog.py", None, None, HitReason.PATH)

    assert context.merge_overlapping_hits([]) == []
    assert context.merge_overlapping_hits([file_hit]) == [file_hit]


def test_merge_restores_exact_priority_after_combining_ranges():
    """测合并后的精确命中仍排在部分命中、路径命中之前。"""
    hits = [
        make_hit("a.py", 1, 2),
        make_hit("z.py", 10, 14),
        make_hit("z.py", 13, 19, HitReason.EQUAL),
        make_hit("0.py", None, None, HitReason.PATH),
    ]

    merged = context.merge_overlapping_hits(hits)

    assert [(h.path, h.reason) for h in merged] == [
        ("z.py", HitReason.EQUAL),
        ("a.py", HitReason.PARTIAL),
        ("0.py", HitReason.PATH),
    ]
    assert (merged[0].start_line, merged[0].end_line) == (10, 19)
    # 合并结果改变了，输入仍可供其他调用复用。
    assert hits[1].end_line == 14
    assert hits[1].reason == HitReason.PARTIAL


def test_small_context_budget_keeps_exact_evidence_first(tmp_path):
    """测预算只能容纳一段时，精确证据不会被弱匹配挤掉。"""
    (tmp_path / "a.py").write_text("weak = 1\n", encoding="utf-8")
    (tmp_path / "z.py").write_text("exact = 1\n", encoding="utf-8")
    hits = [make_hit("a.py", 1, 1), make_hit("z.py", 1, 1, HitReason.EQUAL)]

    merged = context.merge_overlapping_hits(hits)
    result, truncated = context.build_context(tmp_path, merged, max_chars=32)

    assert "exact = 1" in result
    assert "weak = 1" not in result
    assert truncated is True
    assert len(result) <= 32


def test_build_context_reads_only_requested_lines_with_source(tmp_path):
    """行号从 1 开始，输出带来源；不把文件的其他行塞给模型。"""
    (tmp_path / "blog.py").write_text(
        "first = 1\nsecond = 2\nthird = 3\n", encoding="utf-8"
    )

    result, truncated = context.build_context(
        tmp_path, [make_hit("blog.py", 2, 2)], max_chars=200
    )

    assert "blog.py:2-2" in result
    assert "2: second = 2" in result
    assert "first = 1" not in result
    assert "third = 3" not in result
    assert "已截断" not in result
    assert truncated is False


def test_build_context_renders_merged_ranges_without_repeating_lines(tmp_path):
    """先合并重叠命中，再生成片段；同一行只出现一次。"""
    (tmp_path / "blog.py").write_text(
        "first\nsecond\nthird\n", encoding="utf-8"
    )
    hits = [make_hit("blog.py", 1, 2), make_hit("blog.py", 2, 3)]

    merged = context.merge_overlapping_hits(hits)
    result, truncated = context.build_context(tmp_path, merged, max_chars=200)

    assert result.count("2: second") == 1
    assert "1: first" in result
    assert "3: third" in result
    assert truncated is False


def test_build_context_respects_total_budget_and_marks_truncation(tmp_path):
    """预算覆盖标题、行号、正文及截断提示。"""
    (tmp_path / "a.py").write_text("first = 1\n", encoding="utf-8")
    (tmp_path / "z.py").write_text("second = " + "x" * 100 + "\n", encoding="utf-8")
    hits = [make_hit("a.py", 1, 1), make_hit("z.py", 1, 1)]

    result, truncated = context.build_context(tmp_path, hits, max_chars=80)

    assert "first = 1" in result
    assert len(result) <= 80
    assert result.endswith("已截断")
    assert truncated is True


def test_build_context_bounds_one_very_long_line(tmp_path):
    """单行本身超过预算时，也不能输出超过预算的文本。"""
    (tmp_path / "app.py").write_text("payload = " + "x" * 200 + "\n", encoding="utf-8")

    result, truncated = context.build_context(
        tmp_path, [make_hit("app.py", 1, 1)], max_chars=45
    )

    assert len(result) <= 45
    assert result.endswith("已截断")
    assert truncated is True


def test_build_context_previews_file_hit_from_first_line(tmp_path):
    """只有文件路径的命中，从第 1 行开始预览并标对行号。"""
    (tmp_path / "app.py").write_text("first\nsecond\n", encoding="utf-8")

    result, truncated = context.build_context(
        tmp_path, [make_hit("app.py", None, None, HitReason.PATH)], max_chars=200
    )

    assert "app.py" in result
    assert "1: first" in result
    assert "2: second" in result
    assert truncated is False


def test_build_context_empty_hits_return_empty_result(tmp_path):
    """没有命中是正常空结果，不应标记预算截断。"""
    result, truncated = context.build_context(tmp_path, [], max_chars=100)

    assert result == ""
    assert truncated is False
