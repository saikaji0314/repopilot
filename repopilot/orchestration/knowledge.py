"""将版本化资料接到角色上下文、证据失效和明确人工刷新。"""

import json
from pathlib import Path

from langchain_core.tools import tool

from repopilot.collaboration.requests import find_request, open_request, resolve_request
from repopilot.execution.files import Repository
from repopilot.execution.workspace import head_commit, source_snapshot
from repopilot.knowledge.documents import KnowledgeFault, Material, checked_body, digest
from repopilot.knowledge.retrieval import manifest, search
from repopilot.knowledge.store import KnowledgeStore
from repopilot.memory.facts import repository_id
from repopilot.orchestration.state import attach
from repopilot.storage.jobs import JobStore, job_lock


def _candidate_sources(entries, candidate):
    result = {}
    for entry in entries:
        if entry["origin"] != "repository":
            continue
        path = Repository(candidate).resolve(entry["path"], must_exist=False)
        result[entry["id"]] = None if not path.exists() else digest(Repository(candidate).text(entry["path"]))
    return result


def freeze(state, root, candidate):
    if (Path(root) / "knowledge.sqlite3").exists():
        snapshot = manifest(KnowledgeStore(root), state["task"]["repository"], state["run_id"])
    else:
        snapshot = {"repository_id": repository_id(candidate), "entries": []}
    snapshot["workspace_sources"] = _candidate_sources(snapshot["entries"], candidate)
    snapshot.pop("snapshot_id", None)
    retain_snapshot(state, state.get("knowledge_snapshot"))
    state["knowledge_snapshot"] = snapshot | {"snapshot_id": digest(snapshot)}
    retain_snapshot(state, state["knowledge_snapshot"])


def retain_snapshot(state, snapshot):
    if snapshot is None:
        return
    history = state.setdefault("knowledge_history", {})
    history.setdefault(snapshot["snapshot_id"], json.loads(json.dumps(snapshot)))


def validate(state, root):
    snapshot = state.get("knowledge_snapshot")
    if not snapshot:
        return
    if not (Path(root) / "knowledge.sqlite3").exists():
        if snapshot["entries"]:
            raise KnowledgeFault("knowledge_store_unavailable")
        return
    store = KnowledgeStore(root)
    current = manifest(store, state["task"]["repository"], state["run_id"])
    if current["repository_id"] != snapshot["repository_id"] or current["entries"] != snapshot["entries"]:
        raise KnowledgeFault("knowledge_snapshot_changed")
    for entry in snapshot["entries"]:
        # 验证完整历史正文的散列，即使只返回一行也不能忽略缺失／篡改的依据。
        store.read(state["task"]["repository"], entry["id"], version=entry["version"], verify_only=True)
    workspace = attach(state, Path(root))
    if workspace and workspace.path.exists():
        if _candidate_sources(snapshot["entries"], workspace.path) != snapshot["workspace_sources"]:
            raise KnowledgeFault("knowledge_candidate_material_changed")


def sync_answers(state, root, secrets=()):
    """只导出已保存的本地人工答复，不把机器提议提升为确认要求。"""
    if not state.get("workspace_enabled") or not state.get("human_answers"):
        return False
    store, repository = KnowledgeStore(root), state["task"]["repository"]
    done = state.setdefault("knowledge_answers", {})
    changed = False
    for answer in state["human_answers"]:
        key = digest(answer)
        if key in done:
            continue
        actor, channel = answer.get("actor"), answer.get("channel")
        if (actor, channel) not in {("local-operator", "cli"), ("registered-owner", "issue")}:
            raise KnowledgeFault("knowledge_answer_source_untrusted")
        identifier = "answer-" + digest([state["run_id"], key])[:24]
        sources = [f"run:{answer['run_id']}/human:{answer['request_id']}"]
        source = answer.get("source") or {}
        if channel == "issue" and str(source.get("url") or "").startswith("https://"):
            sources.append(source["url"])
        text = "# 人工业务补充\n\n" + "\n".join(answer["questions"]) + "\n\n" + answer["answer"] + "\n"
        material = Material(id=identifier, kind="task",
            title="人工业务补充" if channel == "cli" else "人工业务补充（Issue 评论）",
            required=True, scope=state["run_id"], sources=sources)
        record = store.record(repository, text, material, secrets=secrets)
        if record["status"] != "confirmed":
            record = store.confirm(repository, identifier, version=record["version"], reason="derived_from_local_answer",
                                   source=material.sources[0])
        done[key] = identifier
        changed = True
        # 只有此次明确人工答复派生的资料可增补，其他新确认资料需显式刷新。
        snapshot = state.get("knowledge_snapshot")
        if snapshot is not None:
            retain_snapshot(state, snapshot)
            snapshot["entries"].append({**material.model_dump(mode="json"), "version": record["version"],
                "origin": record["origin"], "path": record["path"], "status": "confirmed"})
            snapshot["entries"].sort(key=lambda item: item["id"])
            snapshot["snapshot_id"] = digest({k: v for k, v in snapshot.items() if k != "snapshot_id"})
            retain_snapshot(state, snapshot)
    return changed


def view(state, root, phase, secrets=()):
    snapshot = state.get("knowledge_snapshot")
    if not snapshot or not snapshot["entries"]:
        return None
    validate(state, root)
    store, repository = KnowledgeStore(root), state["task"]["repository"]
    required = []
    answer_ids = set(state.get("knowledge_answers", {}).values())
    for entry in snapshot["entries"]:
        if entry["required"]:
            if entry["id"] in answer_ids:
                required.append({"id": entry["id"], "version": entry["version"], "provided_in": "human_answers"})
                continue
            body = store.read(repository, entry["id"], version=entry["version"], end_line=200, include_events=False)
            if body["total_lines"] > 200:
                raise KnowledgeFault("knowledge_required_context_too_large")
            required.append(body)
    matches = search(store, repository, state["task"]["request"], scope=state["run_id"])
    result = {"snapshot_id": snapshot["snapshot_id"], "role": phase, "catalog": snapshot["entries"],
              "required": required, "matches": [item for item in matches["entries"] if item["id"] not in answer_ids],
              "omitted": matches.get("omitted", [])}
    encoded = json.dumps(result, ensure_ascii=False)
    checked_body(encoded, secrets)
    if len(encoded.encode("utf-8")) > 24000:
        raise KnowledgeFault("knowledge_required_context_too_large")
    return result


def tools(state, root, secrets=()):
    if not (state.get("knowledge_snapshot") or {}).get("entries"):
        return []

    @tool
    def read_material(material_id: str, version: str, start_line: int = 1, end_line: int = 120) -> dict:
        """读取本次资料清单中的固定版本原文；按行分段，不能读取未授权资料或确认决定。"""
        validate(state, root)
        allowed = {(r["id"], r["version"]) for r in state["knowledge_snapshot"]["entries"]}
        if (material_id, version) not in allowed:
            return {"ok": False, "error": "knowledge_reference_not_in_snapshot"}
        try:
            result = KnowledgeStore(root).read(state["task"]["repository"], material_id, version=version,
                                              start_line=start_line, end_line=end_line, include_events=False)
            checked_body(json.dumps(result, ensure_ascii=False), secrets)
            return {"ok": True, **result}
        except KnowledgeFault as exc:
            return {"ok": False, "error": str(exc)}
    return [read_material]


def record_decisions(state, root, phase, decisions, secrets=()):
    if not decisions or not state.get("workspace_enabled"):
        return
    store = KnowledgeStore(root)
    workspace = attach(state, Path(root))
    code_snapshot = None if workspace is None else source_snapshot(workspace.path)
    for decision in decisions:
        data = decision.model_dump(mode="json")
        identifier = "decision-" + digest([state["run_id"], phase, data])[:24]
        text = f"# {decision.summary}\n\n{decision.reason}\n\n## 来源\n" + "\n".join(decision.sources) + "\n"
        record = store.record(state["task"]["repository"], text,
            Material(id=identifier, kind="decision", title=decision.summary, scope=state["run_id"],
                     sources=[f"run:{state['run_id']}/phase:{phase}", *decision.sources]), secrets=secrets)
        reference = {"id": identifier, "version": record["version"], "phase": phase, "status": record["status"],
                     "knowledge_snapshot_id": (state.get("knowledge_snapshot") or {}).get("snapshot_id"),
                     "code_snapshot": code_snapshot, "modification": state["reworks"]}
        if reference not in state.setdefault("decision_records", []):
            state["decision_records"].append(reference)


def pause(state, root, reason):
    from repopilot.orchestration.human import capture_context
    from repopilot.review import basis as review_basis
    for request in state.get("human_requests", []):
        if request["status"] == "pending" and request["kind"] != "context_update":
            request["status"] = "superseded"
    review_basis.invalidate(state, f"knowledge_{reason}")
    state["in_flight"] = None
    workspace = attach(state, Path(root))
    if workspace and workspace.path.exists():
        state["workspace_snapshot"] = source_snapshot(workspace.path)
    open_request(state, kind="context_update", summary=reason,
        questions=["资料依据已变化或不可用。请核对并确认资料，再用knowledge refresh显式接纳当前版本与现场。"],
        context=capture_context(state, Path(root)))
    state["status"], state["stop_reason"] = "needs_input", reason


def refresh(run_id, root, *, request_id, version, commit, reason):
    from repopilot.orchestration.human import capture_context
    if not run_id or not request_id or not version or not commit or not reason.strip():
        raise KnowledgeFault("knowledge_refresh_arguments_required")
    store = JobStore(root)
    with job_lock(store.root, run_id):
        state = store.get(run_id)
        request = find_request(state, request_id)
        if request.kind != "context_update" or request.status != "pending" or request.version != version:
            raise KnowledgeFault("knowledge_refresh_request_mismatch")
        if state["status"] != "needs_input" or state.get("in_flight"):
            raise KnowledgeFault("knowledge_refresh_not_waiting")
        context = capture_context(state, store.root)
        if any(context[key] != request.context[key] for key in ("task_sha256", "issue_sha256")):
            raise KnowledgeFault("knowledge_refresh_task_changed")
        workspace = attach(state, store.root)
        if workspace is None or head_commit(workspace.path) != commit:
            raise KnowledgeFault("knowledge_refresh_commit_mismatch")
        freeze(state, store.root, workspace.path)
        resolve_request(state, request, action="refresh", text=reason)
        next_phase = "rework" if state.get("rework_pending") or state["phase"] == "rework" else "implement"
        state["phase"], state["status"], state["stop_reason"] = next_phase, "paused", "knowledge_refreshed"
        state["workspace_snapshot"] = source_snapshot(workspace.path)
        state["expected_head"] = commit
        store.save(state)
        return {"status": "paused", "run_id": run_id, "knowledge_snapshot": state["knowledge_snapshot"]}
