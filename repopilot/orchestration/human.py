"""把人工交互接到工作区、版本与任务状态，不在等待期间占用模型会话。"""

from contextlib import contextmanager
from pathlib import Path

from repopilot.collaboration import comments
from repopilot.collaboration import questions as question_batches
from repopilot.collaboration.requests import (
    all_requests, find_request, open_request, put_request, resolve_request,
)
from repopilot.execution.workspace import head_commit, source_snapshot
from repopilot.orchestration.state import attach, canonical_hash
from repopilot.planning import brief as task_brief
from repopilot.storage.jobs import JobStore, StateError, job_lock, timestamp


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
    if state.get("plan"):
        context["plan_version"] = state["plan"]["version"]
    return context


def wait_for_clarification(state, root, *, summary, questions, limit=3, blocking=None):
    """一批集中问题只计一次；到上限后只提交最终缺口，不自动开启下一批。"""
    items = question_batches.clean(questions, summary)
    context = capture_context(state, root)
    version = canonical_hash(context)
    # 同一上下文重复进入等待不再发送，也不重复计数。
    for request in all_requests(state):
        if (request.kind == "clarification" and request.status == "pending"
                and request.version == version):
            return request
    state["question_limit"] = max(1, int(limit))
    used = question_batches.used(state)
    final = used >= state["question_limit"]
    phase = (blocking or {}).get("phase", state["phase"])
    subtask = (blocking or {}).get("subtask")
    request = open_request(state, kind="clarification", summary=summary, questions=items,
        context=context, batch=None if final else used + 1, final=final,
        blocking=question_batches.blocking_items(items, phase, subtask))
    question_batches.record(state, request)
    state["status"] = "needs_input"
    state["stop_reason"] = "question_limit_reached" if final else "human_clarification_required"
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
            "requests": [item.model_dump(mode="json") for item in all_requests(state)],
            "questions": question_batches.summary(state),
            "task_brief": state.get("task_brief")}


def _pending_clarification(state):
    pending = [item for item in all_requests(state)
               if item.kind == "clarification" and item.status == "pending"]
    if not pending:
        raise StateError("no_pending_clarification")
    return pending[-1]


def _apply_answer(state, root, request, *, text, actor, channel, source=None):
    """本地答复与Issue评论共用同一套版本更新和证据失效规则。"""
    from repopilot.orchestration.knowledge import validate
    if request.kind == "context_update":
        raise StateError("use_knowledge_refresh")
    if request.kind != "clarification":
        raise StateError("use_approve_reject_or_recover")
    if (request.status != "pending" or state["status"] != "needs_input"
            or state.get("in_flight") or capture_context(state, root) != request.context):
        raise StateError("stale_human_request")
    validate(state, root)
    resolved = resolve_request(state, request, action="answer", text=text,
                               actor=actor, channel=channel, source=source)
    state["requirements_revision"] = state.get("requirements_revision", 0) + 1
    state.setdefault("human_answers", []).append({
        "run_id": state["run_id"], "request_id": request.id,
        "questions": request.questions, "answer": resolved.reply.text,
        "revision": state["requirements_revision"], "actor": actor,
        "channel": channel, "at": resolved.reply.at, "source": source,
    })
    task_brief.append_answer(state, resolved, text=resolved.reply.text, actor=actor,
                             channel=channel, source=source)
    question_batches.mark_answered(state, resolved)
    # 业务答复会改变审核依据；保留历史，但不沿用旧结论／批准。
    state["verified"], state["review_report"], state["approval"] = [], None, None
    state.pop("approval_digest", None)
    if state["phase"] == "review":
        state["phase"] = "verify"
    state["status"], state["stop_reason"] = "paused", "human_answer_recorded"
    return resolved


def _answer_result(state, request):
    return {"status": state["status"], "run_id": state["run_id"],
            "request": request.model_dump(mode="json"),
            "requirements_revision": state.get("requirements_revision", 0),
            "task_brief_revision": (state.get("task_brief") or {}).get("revision")}


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
        resolved = _apply_answer(state, store.root, request, text=text,
                                 actor="local-operator", channel="cli")
        store.save(state)
        return _answer_result(state, resolved)


@contextmanager
def _forge_client(issue, forge):
    if forge is not None:
        yield forge
        return
    from repopilot.delivery.forge import open_forge
    client = open_forge(issue["url"])
    try:
        yield client
    finally:
        client.close()


def _preflight(state, forge):
    issue = state.get("issue") or {}
    if not issue.get("url"):
        raise StateError("issue_required")
    owner = comments.load_owner()
    result = forge.preflight(issue["url"], owner=owner)
    if (str(result.get("repository") or "").casefold() != str(issue.get("repository") or "").casefold()
            or (result.get("issue") or {}).get("number") != issue.get("number")):
        raise StateError("issue_identity_mismatch")
    return result, owner


def preflight_issue(run_id, root, *, forge=None):
    """只读探查评论与子Issue能力、令牌身份和登记负责人；不发送任何内容。"""
    state = JobStore(root).get(run_id)
    if not (state.get("issue") or {}).get("url"):
        raise StateError("issue_required")
    with _forge_client(state["issue"], forge) as client:
        result, _ = _preflight(state, client)
    return {"status": "ok", "run_id": run_id, "preflight": result}


def _issue_target(state):
    issue = state.get("issue") or {}
    if not issue.get("url"):
        raise StateError("issue_required")
    return issue["repository"], issue["number"]


def _delivery(state, request, **updates):
    delivery = {**(request.delivery or {}), **updates}
    current = find_request(state, request.id)
    updated = current.model_copy(update={"delivery": delivery})
    put_request(state, updated)
    return updated


def post_issue(run_id, root, *, forge=None):
    """把当前待答批次写成一条父Issue评论；同一标记已存在时复用，不重复发送。"""
    store = JobStore(root)
    with job_lock(store.root, run_id):
        state = store.get(run_id)
        request = _pending_clarification(state)
        if (request.delivery or {}).get("status") in {"delivered", "reused"}:
            return {"status": "ok", "run_id": run_id, "request": request.model_dump(mode="json"),
                    "delivery": request.delivery, "reused": True}
        repository, number = _issue_target(state)
        with _forge_client(state["issue"], forge) as client:
            preflight, _ = _preflight(state, client)
            state["platform_preflight"] = preflight
            if (preflight.get("comments") or {}).get("status") != "supported":
                store.save(state)
                raise StateError("issue_comments_unavailable")
            if not preflight.get("viewer"):
                store.save(state)
                raise StateError("platform_viewer_unavailable")
            marker_text = comments.marker(request)
            existing = client.find_issue_comment(repository, number, marker_text)
            if existing is not None:
                updated = _delivery(state, request, status="reused", channel="issue",
                    provider=preflight.get("provider"), host=preflight.get("host"),
                    marker=marker_text, **comments.comment_source(existing), reused_at=timestamp())
                store.save(state)
                return {"status": "ok", "run_id": run_id, "request": updated.model_dump(mode="json"),
                        "delivery": updated.delivery, "reused": True}
            body = comments.batch_body(state, request)
            # 先保存发送意图与标记，再执行外部写入；重试时先查同一标记。
            updated = _delivery(state, request, status="sending", channel="issue",
                provider=preflight.get("provider"), host=preflight.get("host"),
                marker=marker_text, intended_at=timestamp())
            store.save(state)
            try:
                posted = client.add_issue_comment(repository, number, body)
            except Exception as exc:
                _delivery(state, updated, status="failed", error_type=type(exc).__name__,
                          failed_at=timestamp())
                store.save(state)
                raise
            updated = _delivery(state, updated, status="delivered", channel="issue",
                provider=preflight.get("provider"), host=preflight.get("host"),
                marker=marker_text, **comments.comment_source(posted), posted_at=timestamp())
            store.save(state)
            return {"status": "ok", "run_id": run_id, "request": updated.model_dump(mode="json"),
                    "delivery": updated.delivery}


def sync_issue(run_id, root, *, forge=None):
    """读取批次评论之后的第一条有效负责人答复；其他人的评论不推进任务。"""
    store = JobStore(root)
    with job_lock(store.root, run_id):
        state = store.get(run_id)
        pending = [item for item in all_requests(state)
                   if item.kind == "clarification" and item.status == "pending"]
        answered = [item for item in all_requests(state)
                    if item.kind == "clarification" and item.status == "answered"
                    and (item.delivery or {}).get("status") == "answered"]
        if pending:
            request = pending[-1]
        elif answered:
            request = answered[-1]
            return {"status": state["status"], "run_id": run_id, "synced": True,
                    "already_answered": True, "request": request.model_dump(mode="json"),
                    "requirements_revision": state.get("requirements_revision", 0),
                    "task_brief_revision": (state.get("task_brief") or {}).get("revision")}
        else:
            raise StateError("no_pending_clarification")
        delivery = request.delivery or {}
        if delivery.get("status") not in {"delivered", "reused"}:
            raise StateError("question_batch_not_posted")
        repository, number = _issue_target(state)
        with _forge_client(state["issue"], forge) as client:
            preflight, owner = _preflight(state, client)
            state["platform_preflight"] = preflight
            if (preflight.get("comments") or {}).get("status") != "supported":
                store.save(state)
                raise StateError("issue_comments_unavailable")
            values = client.issue_comments(repository, number)
            reply, later = comments.select_reply(values, request, owner=owner)
            if reply is None:
                # 没有批次评论或没有负责人答复都不算同意；只记录本次只读核对。
                _delivery(state, request, last_checked_at=timestamp(),
                          last_checked_comment_count=len(values),
                          ignored_replies=later)
                store.save(state)
                return {"status": "needs_input", "run_id": run_id, "synced": False,
                        "reason": "no_registered_owner_reply",
                        "request": find_request(state, request.id).model_dump(mode="json")}
            resolved = _apply_answer(state, store.root, request, text=reply["text"],
                                     actor="registered-owner", channel="issue", source=reply)
            _delivery(state, resolved, status="answered", channel="issue",
                      answer_comment_id=reply.get("comment_id"), answer_url=reply.get("url"),
                      answered_at=timestamp(), ignored_replies=later)
            store.save(state)
            return {**_answer_result(state, find_request(state, request.id)), "synced": True,
                    "reply": reply, "ignored_replies": later}
