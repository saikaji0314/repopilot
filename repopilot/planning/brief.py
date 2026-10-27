"""任务说明的版本保存与人工答复追加；不依赖模型、Git 或平台。"""

import json

from repopilot.storage.jobs import timestamp
from .contracts import digest, normalize


def _core(brief):
    return {key: value for key, value in brief.items() if key not in {"sha256", "history"}}


def initial(task):
    brief = {"schema_version": 1, "revision": 0, "goal": task.request, "acceptance": [],
             "assumptions": [], "out_of_scope": [], "unresolved": [], "resolved": [],
             "sources": [], "updated_at": timestamp()}
    brief["sha256"] = digest(_core(brief))
    return brief


def ensure(state, task=None):
    """旧运行没有任务说明时补一份初始版本，不改变原有批准摘要规则。"""
    if state.get("task_brief"):
        return state["task_brief"]
    if task is None:
        from repopilot.core.config import Task
        task = Task.model_validate(state["task"])
    brief = initial(task)
    state["task_brief"] = brief
    state.setdefault("task_brief_history", {})[brief["sha256"]] = json.loads(json.dumps(brief))
    return brief


def project(brief, fields):
    """按规划结果生成下一版任务说明，保留已有人工答复记录。"""
    data = fields.model_dump(mode="json") if hasattr(fields, "model_dump") else dict(fields)
    sources = []
    for source in [*(brief.get("sources") or []), *(data.get("sources") or [])]:
        if source not in sources:
            sources.append(source)
    projected = {"schema_version": 1, "revision": int(brief.get("revision") or 0) + 1,
                 "goal": data["goal"], "acceptance": list(data["acceptance"]),
                 "assumptions": list(data.get("assumptions") or []),
                 "out_of_scope": list(data.get("out_of_scope") or []),
                 "unresolved": list(data.get("unresolved") or []),
                 "resolved": json.loads(json.dumps(brief.get("resolved") or [])),
                 "sources": sources[:12], "updated_at": timestamp()}
    projected["sha256"] = digest(_core(projected))
    return projected


def commit(state, brief):
    state.setdefault("task_brief_history", {})[brief["sha256"]] = json.loads(json.dumps(brief))
    state["task_brief"] = brief
    return brief


def append_answer(state, request, *, text, actor, channel, source=None):
    """把一次有效业务答复写进任务说明的新版本，并移除对应未决问题。"""
    current = ensure(state)
    asked = {normalize(item) for item in request.questions}
    resolved = json.loads(json.dumps(current.get("resolved") or []))
    resolved.append({"request_id": request.id, "batch": request.batch, "questions": list(request.questions),
                     "answer": text, "actor": actor, "channel": channel,
                     "source": source, "answered_at": timestamp(),
                     "brief_revision": int(current.get("revision") or 0) + 1})
    unresolved = [item for item in current.get("unresolved") or [] if normalize(item) not in asked]
    updated = {**current, "revision": int(current.get("revision") or 0) + 1,
               "resolved": resolved, "unresolved": unresolved, "updated_at": timestamp()}
    updated.pop("sha256", None)
    updated["sha256"] = digest(_core(updated))
    return commit(state, updated)
