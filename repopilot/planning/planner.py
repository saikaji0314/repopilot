"""计划校验、依赖顺序与执行进度；把模型报告变成可执行事实。"""

from repopilot.execution.workspace import run_git
from repopilot.storage.jobs import timestamp
from . import brief as brief_store
from .contracts import PlanFault, TaskPlan, digest


def topological_order(subtasks):
    """稳定拓扑排序；缺失依赖或环都拒绝执行，不按声明顺序硬跑。"""
    known = {item.id: item for item in subtasks}
    if len(known) != len(subtasks):
        raise PlanFault("duplicate_subtask_id")
    indegree, dependents = {item.id: 0 for item in subtasks}, {item.id: [] for item in subtasks}
    for item in subtasks:
        for dependency in item.depends_on:
            if dependency == item.id:
                raise PlanFault("dependency_cycle")
            if dependency not in known:
                raise PlanFault("unknown_dependency")
            indegree[item.id] += 1
            dependents[dependency].append(item.id)
    ready = [item.id for item in subtasks if indegree[item.id] == 0]
    order = []
    while ready:
        current = ready.pop(0)
        order.append(current)
        for dependent in dependents[current]:
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)
    if len(order) != len(subtasks):
        raise PlanFault("dependency_cycle")
    return order


def build_plan(state, report, *, max_subtasks, source="planner"):
    """校验报告并返回(计划, 下一版任务说明)；调用方负责再写入状态。"""
    if not report.subtasks:
        raise PlanFault("missing_subtasks")
    if len(report.subtasks) > max_subtasks:
        raise PlanFault("too_many_subtasks")
    order = topological_order(report.subtasks)
    projected = brief_store.project(brief_store.ensure(state), report.brief)
    content = {"brief_revision": projected["revision"], "brief_sha256": projected["sha256"],
               "split": report.split, "split_reason": report.split_reason, "summary": report.summary,
               "subtasks": [item.model_dump(mode="json") for item in report.subtasks],
               "order": order, "decisions": [item.model_dump(mode="json") for item in report.decisions]}
    plan_hash = digest(content)
    plan = TaskPlan.model_validate({
        "schema_version": 1, "version": f"plan-{plan_hash[:12]}", "hash": plan_hash,
        "brief_revision": projected["revision"], "brief_sha256": projected["sha256"],
        "split": report.split, "split_reason": report.split_reason, "summary": report.summary,
        "subtasks": [item.model_dump(mode="json") for item in report.subtasks], "order": order,
        "decisions": [item.model_dump(mode="json") for item in report.decisions],
        "created_at": timestamp(), "source": source})
    return plan, projected


def record(state, plan, projected):
    """保存计划与任务说明版本；重复记录同一计划不追加历史。"""
    history = state.setdefault("plan_history", [])
    if not any(item.get("hash") == plan.hash for item in history):
        history.append(plan.model_dump(mode="json"))
    state["plan"] = plan.model_dump(mode="json")
    state["plan_progress"] = {"order": list(plan.order), "current": None, "results": [],
                              "started_at": timestamp()}
    # 重规划后把新计划版本关联到触发它的旧计划记录，实现载荷可判断旧工作上下文。
    if state.get("plan_refresh_required") and state.get("plan_refresh_history"):
        latest = state["plan_refresh_history"][-1]
        latest["new_plan"] = plan.version
        latest["replanned_at"] = timestamp()
    state.pop("plan_refresh_required", None)
    brief_store.commit(state, projected)
    return state["plan"]


def _result_view(item):
    return {"id": item.get("id"), "title": item.get("title"), "status": item.get("status"),
            "summary": str(item.get("summary") or "")[:500], "commit": item.get("commit")}


def refresh_context(state):
    """返回当前计划对应的旧计划／旧进度上下文；没有重规划时返回None。"""
    history = state.get("plan_refresh_history") or []
    if not history:
        return None
    latest = history[-1]
    current = (state.get("plan") or {}).get("version")
    if not state.get("plan_refresh_required") and latest.get("new_plan") != current:
        return None
    old_plan = next((item for item in state.get("plan_history") or []
                     if item.get("version") == latest.get("old_plan")), None)
    progress = latest.get("old_progress") or {}
    old_progress = {"order": progress.get("order"), "current": progress.get("current"),
                    "started_at": progress.get("started_at"),
                    "results": [_result_view(item) for item in progress.get("results") or []]}
    return {"old_plan_version": latest.get("old_plan"),
            "old_plan": old_plan,
            "old_progress": old_progress,
            "old_brief_revision": latest.get("old_brief_revision"),
            "new_brief_revision": latest.get("new_brief_revision"),
            "request_id": latest.get("request_id"),
            "note": ("任务说明在上一版计划执行后发生变化。工作区仍保留上一版计划已提交的改动；"
                     "先核对旧实现和完成项，只修改受影响部分，不要静默回退旧工作。")}


def result_map(state):
    return {item.get("id"): item for item in
            (state.get("plan_progress") or {}).get("results") or []}


def completed_ids(state):
    return {identifier for identifier, item in result_map(state).items()
            if item.get("status") == "completed"}


def current_subtask(state):
    plan, progress = state.get("plan"), state.get("plan_progress") or {}
    if not plan:
        raise PlanFault("plan_missing")
    nodes = {item["id"]: item for item in plan["subtasks"]}
    current = progress.get("current")
    if current:
        if current not in nodes:
            raise PlanFault("plan_progress_unknown_task")
        if current not in completed_ids(state):
            return nodes[current]
        progress["current"] = None
    pending = [identifier for identifier in progress["order"] if identifier not in completed_ids(state)]
    return None if not pending else nodes[pending[0]]


def dependencies_done(state, subtask, worktree=None):
    """前置子任务必须已完成，且其产物确实在当前工作区历史里。"""
    results = result_map(state)
    for dependency in subtask.get("depends_on") or []:
        result = results.get(dependency)
        if not result or result.get("status") != "completed":
            raise PlanFault("dependency_not_completed")
        commit = str(result.get("commit") or "")
        if worktree is not None:
            if not commit:
                raise PlanFault("dependency_artifact_missing")
            outcome = run_git(worktree, "merge-base", "--is-ancestor", commit, "HEAD", check=False)
            if outcome.returncode != 0:
                raise PlanFault("dependency_artifact_missing")
    return True


def set_current(state, subtask_id):
    progress = state.setdefault("plan_progress", {"order": [], "current": None, "results": []})
    progress["current"] = subtask_id


def record_result(state, subtask, *, status, summary, commit=None, evidence=None, decisions=None,
                  started_at=None):
    progress = state.setdefault("plan_progress", {"order": [], "current": None, "results": []})
    record = {"id": subtask["id"], "title": subtask["title"], "status": status, "summary": summary,
              "commit": commit, "evidence": list(evidence or []), "decisions": list(decisions or []),
              "started_at": started_at, "finished_at": timestamp()}
    progress["results"] = [item for item in progress.get("results") or [] if item.get("id") != subtask["id"]]
    progress["results"].append(record)
    progress["results"].sort(key=lambda item: progress["order"].index(item["id"]))
    progress["current"] = None
    return record


def status(state):
    """用结果事实描述计划执行到哪一步，不把“最后一个报告完成”当成全部完成。"""
    plan = state.get("plan")
    if not plan:
        return {"state": "absent", "completed": 0, "total": 0}
    results = result_map(state)
    completed = [identifier for identifier in plan["order"] if results.get(identifier, {}).get("status") == "completed"]
    blocked = [item for item in results.values() if item.get("status") not in {"completed"}]
    if len(completed) == len(plan["order"]):
        state_name = "completed"
    elif blocked:
        state_name = "blocked"
    else:
        state_name = "in_progress"
    return {"state": state_name, "completed": len(completed), "total": len(plan["order"]),
            "current": (state.get("plan_progress") or {}).get("current"),
            "version": plan["version"]}


def payload(state):
    """给实现／审核角色的有界计划视图；完整计划仍可从状态与报告回查。"""
    plan = state.get("plan")
    if not plan:
        return None
    results = result_map(state)
    brief = state.get("task_brief") or {}
    current_revision = int(brief.get("revision") or 0)
    return {"version": plan["version"], "split": plan["split"], "reason": plan["split_reason"],
            "summary": plan["summary"], "order": plan["order"],
            "brief_revision": plan.get("brief_revision"),
            "brief_sha256": plan.get("brief_sha256"),
            "current_brief_revision": current_revision,
            "stale": current_revision != int(plan.get("brief_revision") or 0),
            "subtasks": [{"id": item["id"], "title": item["title"], "goal": item["goal"],
                          "acceptance": item["acceptance"], "boundaries": item["boundaries"],
                          "depends_on": item["depends_on"],
                          "result": None if item["id"] not in results else {
                              "status": results[item["id"]].get("status"),
                              "summary": str(results[item["id"]].get("summary") or "")[:500],
                              "commit": results[item["id"]].get("commit")}}
                         for item in plan["subtasks"]]}
