# JSON/JSONL 轨迹保存
from pathlib import Path
import json
import time
from enum import StrEnum
from pydantic import BaseModel


def _check_file(filename: Path | str):
    if isinstance(filename, str):
        filename = Path(filename)
    if not filename.parent.exists():
        filename.parent.mkdir(parents=True, exist_ok=True)
    if not filename.exists():
        filename.touch()


def save_trace(filename: Path | str | None, trace: list[dict] | dict | list[BaseModel] | BaseModel | str):
    if filename is None:
        return
    _check_file(filename)

    if isinstance(trace, dict):
        filename.write_text(json.dumps(trace, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    elif isinstance(trace, list):
        content = []
        for item in trace:
            if isinstance(item, BaseModel):
                content.append(item.model_dump())
            else:
                content.append(item)
        filename.write_text(json.dumps(content, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    elif isinstance(trace, BaseModel):
        filename.write_text(trace.model_dump_json() + "\n", encoding="utf-8")
    elif isinstance(trace, str):
        filename.write_text(trace + "\n", encoding="utf-8")
    else:
        raise ValueError(f"Unknown trace content type: {type(trace)}")


class TraceFilename(StrEnum):
    TASK = "task.json"
    MESSAGES = "messages.json"
    TRACE = "trace.jsonl"
    FINAL_ANSWER = "final_answer.json"
    SUMMARY = "summary.json"


class TraceEvent(StrEnum):
    RUN_STARTED = "run_started"
    MODEL_TURN_COMPLETED = "model_turn_completed"
    TOOL_CALL_STARTED = "tool_call_started"
    TOOL_CALL_COMPLETED = "tool_call_completed"
    RUN_FINISHED = "run_finished"


def save_trace_event(filename: Path | str | None, event: TraceEvent, data: dict | BaseModel | str):
    if filename is None:
        return
    _check_file(filename)
    with open(filename, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "datetime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "event": event.value,
            "data": data.model_dump() if isinstance(data, BaseModel) else data,
        }, ensure_ascii=False) + "\n")


def redact_secrets(value, secrets):
    """递归替换字符串中的已知密钥，不改变原始对象。"""
    if isinstance(value, str):
        for secret in secrets:
            if secret:  # 必须跳过 None 和空字符串
                value = value.replace(secret, "[REDACTED]")
        return value

    if isinstance(value, dict):
        return {
            key: redact_secrets(item, secrets)
            for key, item in value.items()
        }

    if isinstance(value, list):
        return [redact_secrets(item, secrets) for item in value]

    return value
