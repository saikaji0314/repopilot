"""人工请求的数据与生命周期，同任务检查点一起持久化。"""

from typing import Literal

from pydantic import Field

from repopilot.core.config import Contract
from repopilot.orchestration.state import canonical_hash
from repopilot.storage.jobs import StateError, timestamp


class HumanReply(Contract):
    action: Literal["answer", "approve", "reject", "recover", "refresh"]
    text: str = Field(min_length=1, max_length=8000)
    actor: Literal["local-operator"] = "local-operator"
    channel: Literal["cli"] = "cli"
    at: str


class HumanRequest(Contract):
    schema_version: Literal[1] = 1
    id: str
    kind: Literal["clarification", "approval", "recovery", "context_update"]
    status: Literal["pending", "answered", "superseded"] = "pending"
    phase: str
    summary: str
    questions: list[str]
    context: dict
    version: str
    created_at: str
    reply: HumanReply | None = None


def all_requests(state):
    return [HumanRequest.model_validate(item) for item in state.get("human_requests", [])]


def find_request(state, request_id):
    for request in all_requests(state):
        if request.id == request_id:
            return request
    raise StateError("human_request_not_found")


def put_request(state, request):
    records = state.setdefault("human_requests", [])
    data = request.model_dump(mode="json")
    for index, item in enumerate(records):
        if item["id"] == request.id:
            records[index] = data
            return
    records.append(data)


def open_request(state, *, kind, summary, questions, context):
    """同一上下文重复进入等待不再建请求，旧上下文的同类请求保留但失效。"""
    version = canonical_hash(context)
    for request in all_requests(state):
        if request.kind != kind or request.status != "pending":
            continue
        if request.version == version:
            return request
        put_request(state, request.model_copy(update={"status": "superseded"}))
    number = len(state.get("human_requests", [])) + 1
    request = HumanRequest(id=f"human-{number}-{version[:12]}", kind=kind,
        phase=state["phase"], summary=summary, questions=questions,
        context=context, version=version, created_at=timestamp())
    put_request(state, request)
    return request


def resolve_request(state, request, *, action, text):
    text = text.strip()
    if not text:
        raise StateError("human_reply_required")
    reply = HumanReply(action=action, text=text, at=timestamp())
    resolved = request.model_copy(update={"status": "answered", "reply": reply})
    put_request(state, resolved)
    return resolved
