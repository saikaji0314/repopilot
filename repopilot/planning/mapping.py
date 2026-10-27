"""内部计划到外部子Issue的映射：按需创建、标记去重、能力不支持时保留本地计划。"""

from repopilot.storage.jobs import JobStore, StateError, job_lock, timestamp
from repopilot.planning.contracts import PlanFault
from . import planner


def marker(state, subtask):
    return f"<!-- repopilot-subtask:{state['run_id']}:{subtask['id']} -->"


def existing(state, subtask_id):
    for item in state.get("subtask_mappings") or []:
        if item.get("subtask") == subtask_id:
            return item
    return None


def _record(state, subtask, **values):
    current = existing(state, subtask["id"]) or {"subtask": subtask["id"], "created_at": timestamp()}
    current.update(values)
    records = state.setdefault("subtask_mappings", [])
    records[:] = [item for item in records if item.get("subtask") != subtask["id"]]
    records.append(current)
    records.sort(key=lambda item: str(item.get("subtask")))
    return current


def _title(state, subtask):
    issue = state["issue"]
    title = f"[RepoPilot] {issue['repository']}#{issue['number']} {subtask['title']}"
    return title[:200]


def _body(state, subtask, marker_text):
    issue = state["issue"]
    lines = [marker_text, "## RepoPilot 内部计划子任务", "",
             f"- 父Issue：{issue['url']}",
             f"- 计划版本：`{state['plan']['version']}`",
             f"- 修改边界：{'；'.join(subtask['boundaries'])}",
             f"- 依赖子任务：{'、'.join(subtask.get('depends_on') or []) or '无'}",
             f"- 映射理由：{subtask.get('track_reason') or ''}", "",
             "### 目标", subtask["goal"], "", "### 验收"]
    lines.extend(f"- {item}" for item in subtask["acceptance"])
    lines.extend(["", "这是内部计划的追踪映射，不代表交付批准；合并与发布仍需人工决定。"])
    return "\n".join(lines)


def map_subtasks(run_id, root, forge):
    """创建或复用计划需要的子Issue；重复执行不重复创建。"""
    store = JobStore(root)
    with job_lock(store.root, run_id):
        state = store.get(run_id)
        plan = state.get("plan")
        if not plan:
            raise PlanFault("plan_required")
        issue = state.get("issue") or {}
        if not issue.get("url"):
            raise StateError("issue_required_for_subtask_mapping")
        tracked = [item for item in plan["subtasks"] if item.get("track") == "issue"]
        if not tracked:
            return {"status": "ok", "run_id": run_id, "created": 0, "reused": 0,
                    "mappings": list(state.get("subtask_mappings") or [])}
        preflight = forge.preflight(issue["url"], owner=None)
        if (str(preflight.get("repository") or "").casefold() != str(issue.get("repository") or "").casefold()
                or (preflight.get("issue") or {}).get("number") != issue.get("number")):
            raise StateError("issue_identity_mismatch")
        state["platform_preflight"] = preflight
        creation = (preflight.get("issue_creation") or {}).get("status")
        native = (preflight.get("native_sub_issues") or {}).get("status") == "supported"
        created = reused = 0
        for subtask in tracked:
            current = existing(state, subtask["id"])
            if current and current.get("status") in {"created", "reused"} and current.get("issue"):
                reused += 1
                continue
            marker_text = marker(state, subtask)
            _record(state, subtask, status="creating", kind=None, marker=marker_text,
                    plan_version=plan["version"], parent_issue=issue["url"], note="")
            store.save(state)
            found = forge.find_issue_by_marker(issue["repository"], marker_text)
            if found is not None:
                if found.get("number") == issue.get("number"):
                    _record(state, subtask, status="failed", kind=None, marker=marker_text,
                            plan_version=plan["version"], parent_issue=issue["url"],
                            note="marker appeared on the parent issue; refusing to reuse it")
                    store.save(state)
                    raise PlanFault("subtask_marker_matches_parent_issue")
                _record(state, subtask, status="reused", kind="linked", issue=found,
                        marker=marker_text, plan_version=plan["version"], parent_issue=issue["url"],
                        note="found existing marker; native relationship not recreated")
                store.save(state)
                reused += 1
                continue
            if creation == "unsupported":
                _record(state, subtask, status="local_only", kind="local", marker=marker_text,
                        plan_version=plan["version"], parent_issue=issue["url"],
                        note="platform reports issue creation unsupported")
                store.save(state)
                continue
            try:
                value = forge.create_issue(issue["repository"],
                                           title=_title(state, subtask),
                                           body=_body(state, subtask, marker_text))
            except Exception as exc:
                _record(state, subtask, status="failed", kind=None, marker=marker_text,
                        plan_version=plan["version"], parent_issue=issue["url"],
                        error_type=type(exc).__name__)
                store.save(state)
                raise
            relationship = {"native": False,
                            "reason": (preflight.get("native_sub_issues") or {}).get("evidence")
                                      or "platform_native_sub_issues_unavailable"}
            if not value.get("url") or (native and not value.get("id")):
                _record(state, subtask, status="failed", kind=None, marker=marker_text,
                        plan_version=plan["version"], parent_issue=issue["url"],
                        note="platform response lacks issue identity")
                store.save(state)
                raise PlanFault("subtask_issue_identity_missing")
            if native:
                relationship = forge.link_sub_issue(issue["repository"], issue["number"], value["id"])
            _record(state, subtask, status="created",
                    kind="native" if relationship.get("native") else "linked",
                    issue=value, marker=marker_text, plan_version=plan["version"],
                    parent_issue=issue["url"], note="" if relationship.get("native") else
                    relationship.get("reason") or "platform stores linked issue mapping")
            store.save(state)
            created += 1
        return {"status": "ok", "run_id": run_id, "created": created, "reused": reused,
                "native_sub_issues": native, "issue_creation": creation,
                "mappings": list(state.get("subtask_mappings") or [])}


def show(run_id, root):
    """本地只读查看计划、进度与映射；不加载平台凭证。"""
    state = JobStore(root).get(run_id)
    return {"status": "ok", "run_id": run_id, "task_brief": state.get("task_brief"),
            "plan": state.get("plan"), "plan_status": planner.status(state),
            "plan_progress": state.get("plan_progress"),
            "plan_history": state.get("plan_history") or [],
            "subtask_mappings": state.get("subtask_mappings") or [],
            "platform_preflight": state.get("platform_preflight")}
