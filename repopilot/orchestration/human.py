"""把人工交互接到工作区、版本与任务状态，不在等待期间占用模型会话。"""

from pathlib import Path

from repopilot.collaboration.requests import (
    all_requests, find_request, open_request, resolve_request,
)
from repopilot.execution.workspace import head_commit, source_snapshot
from repopilot.orchestration.state import attach, canonical_hash
from repopilot.storage.jobs import JobStore, StateError, job_lock


def capture_context(state, root):
    workspace = attach(state, root)
    path = workspace.path if workspace and workspace.path.exists() else Path(state["task"]["repository"])
    context = {
        "run_id": state["run_id"],
        "task_sha256": canonical_hash(state["task"]),
        "issue_sha256": canonical_hash(state.get("issue")),
        "requirements_revision": state.get("requirements_revision", 0),
        "answers_sha256": canonical_hash(state.get("human_answers", [])),
        "phase": state["phase"], "base_commit": state.get("base_commit"),
        "head_commit": head_commit(path), "workspace_sha256": source_snapshot(path),
        "approval_digest": state.get("approval_digest"),
    }
    if (state.get("knowledge_snapshot") or {}).get("entries"):
        context["knowledge_snapshot_id"] = state["knowledge_snapshot"]["snapshot_id"]
    return context


def wait_for_clarification(state, root, *, summary, questions):
    request = open_request(state, kind="clarification", summary=summary,
        questions=questions or [summary], context=capture_context(state, root))
    state["status"], state["stop_reason"] = "needs_input", "human_clarification_required"
    return request


def approval_request(state, root):
    return open_request(state, kind="approval", summary="核对固定提交、需求和验证证据后决定是否允许交付草稿。",
        questions=["是否批准此提交创建草稿 PR／MR？"], context=capture_context(state, root))


def recovery_request(state, root):
    return open_request(state, kind="recovery", summary="阶段中断，需要核对实际工作区后显式恢复。",
        questions=["现场是否可从当前提交继续？请使用 recover 指定提交与核对理由。"],
        context=capture_context(state, root))


def list_requests(run_id, root):
    state = JobStore(root).get(run_id)
    return {"status": "ok", "run_id": run_id,
            "requests": [item.model_dump(mode="json") for item in all_requests(state)]}


def answer(run_id, root, *, request_id, version, text):
    """只补充业务信息；不调用模型、不批准、不推送，之后显式 resume。"""
    store = JobStore(root)
    with job_lock(store.root, run_id):
        state = store.get(run_id)
        request = find_request(state, request_id)
        if request.kind == "context_update":
            raise StateError("use_knowledge_refresh")
        if request.kind != "clarification":
            raise StateError("use_approve_reject_or_recover")
        if version != request.version:
            raise StateError("human_request_version_mismatch")
        if request.status == "answered":
            if request.reply.action != "answer" or request.reply.text != text.strip():
                raise StateError("human_request_already_answered")
            return _answer_result(state, request)
        if (request.status != "pending" or state["status"] != "needs_input"
                or state.get("in_flight") or capture_context(state, store.root) != request.context):
            raise StateError("stale_human_request")
        from repopilot.orchestration.knowledge import validate
        validate(state, store.root)
        request = resolve_request(state, request, action="answer", text=text)
        state["requirements_revision"] = state.get("requirements_revision", 0) + 1
        state.setdefault("human_answers", []).append({
            "run_id": run_id, "request_id": request.id,
            "questions": request.questions, "answer": request.reply.text,
            "revision": state["requirements_revision"], "actor": request.reply.actor,
            "channel": request.reply.channel, "at": request.reply.at,
        })
        # 业务答复会改变审核依据；保留历史，但不沿用旧结论／批准。
        state["verified"], state["review_report"], state["approval"] = [], None, None
        state.pop("approval_digest", None)
        if state["phase"] == "review":
            state["phase"] = "verify"
        state["status"], state["stop_reason"] = "paused", "human_answer_recorded"
        store.save(state)
        return _answer_result(state, request)


def _answer_result(state, request):
    return {"status": state["status"], "run_id": state["run_id"],
            "request": request.model_dump(mode="json"),
            "requirements_revision": state.get("requirements_revision", 0)}
