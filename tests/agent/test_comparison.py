"""对照指标测试：只统计真正返回的代码，不以 requested 范围冒充证据。"""
import json
from scripts.compare_retrieval import measure_run


def test_metrics_use_returned_lines_and_keep_failed_attempt(tmp_path):
    summary = {"status": "failed", "stop_reason": "max_tool_calls", "model_steps": 2,
               "tool_calls": 2, "usage": {"total_tokens": 100},
               "estimated_cost_cny": 0.01, "elapsed_seconds": 3}
    (tmp_path / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    (tmp_path / "runtime.json").write_text('{"index_seconds":0}', encoding="utf-8")
    events = []
    for number in (1, 2):
        events.extend([
            {"event": "tool_call_started", "data": {"tool_call_id": str(number),
             "tool": "read_file", "arguments_json": '{"path":"a.py","start_line":1,"end_line":20}'}},
            {"event": "tool_call_completed", "data": {"tool_call_id": str(number),
             "result": {"ok": True, "tool": "read_file", "data": {"content": "1: actual\n已截断"}}}},
        ])
    (tmp_path / "trace.jsonl").write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    metrics = measure_run(tmp_path, {"evidence_targets": [
        {"path": "a.py", "start_line": 1, "end_line": 1},
        {"path": "a.py", "start_line": 10, "end_line": 10},
    ]})
    assert metrics["status"] == "failed"
    assert metrics["observed_source_lines"] == 2
    assert metrics["unique_source_lines"] == 1
    assert metrics["repeated_source_line_fraction"] == 0.5
    assert metrics["evidence_ranges_hit"] == 1
    assert metrics["evidence_line_coverage"] == 0.5
