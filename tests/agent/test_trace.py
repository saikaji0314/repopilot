"""步骤 6 第一组：JSON 快照和 JSONL 事件的保存契约。"""

import json

import pytest

from repopilot.legacy.agent.schemas import Usage
from repopilot.legacy.agent.trace import TraceEvent, save_trace, save_trace_event


@pytest.mark.parametrize("payload", [[], [{"role": "user", "content": "你好"},
                                        {"role": "assistant", "content": "已收到"}]])
def test_snapshot_preserves_entire_list(tmp_path, payload):
    """测整个列表保存为一个 JSON 数组，空列表也必须是合法 JSON。"""
    path = tmp_path / "nested" / "messages.json"
    save_trace(path, payload)

    assert json.loads(path.read_text(encoding="utf-8")) == payload


def test_snapshot_accepts_dict_and_overwrites_old_snapshot(tmp_path):
    """测 summary 字典可以保存；第二次写入替换旧快照。"""
    path = tmp_path / "summary.json"
    save_trace(path, {"status": "running", "old": True})
    save_trace(path, {"status": "completed"})

    assert json.loads(path.read_text(encoding="utf-8")) == {"status": "completed"}


def test_snapshot_serializes_model_list(tmp_path):
    """测 Pydantic 对象列表不会只留下最后一个对象。"""
    values = [Usage(total_tokens=1), Usage(total_tokens=2)]
    path = tmp_path / "usage.json"
    save_trace(path, values)

    assert json.loads(path.read_text(encoding="utf-8")) == [v.model_dump() for v in values]


def test_events_append_one_json_object_per_line(tmp_path):
    """测事件按调用顺序追加，每一行都能单独解析，中文使用 UTF-8。"""
    path = tmp_path / "trace.jsonl"
    save_trace_event(path, TraceEvent.RUN_STARTED, {"note": "开始"})
    save_trace_event(path, TraceEvent.RUN_FINISHED, {"note": "结束"})

    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [event["event"] for event in events] == ["run_started", "run_finished"]
    assert [event["data"]["note"] for event in events] == ["开始", "结束"]
    assert all(event["datetime"] for event in events)


def test_event_accepts_pydantic_data(tmp_path):
    """测事件内部的 Pydantic data 也能转换为 JSON 对象。"""
    path = tmp_path / "trace.jsonl"
    save_trace_event(path, TraceEvent.MODEL_TURN_COMPLETED, Usage(total_tokens=3))

    assert json.loads(path.read_text(encoding="utf-8"))["data"]["total_tokens"] == 3


def test_none_path_disables_writing(tmp_path, monkeypatch):
    """测关闭保存不会创建文件；Loop 还需避免提前计算 None / filename。"""
    monkeypatch.chdir(tmp_path)
    save_trace(None, [{"content": "不落盘"}])
    save_trace_event(None, TraceEvent.RUN_STARTED, {})

    assert list(tmp_path.iterdir()) == []
