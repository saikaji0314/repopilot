"""跨阶段共享的状态规则与工作区定位，不执行模型或修改检查点。"""

from hashlib import sha256
import json
from pathlib import Path

from repopilot.execution.workspace import Workspace


TERMINAL = {"completed", "published", "rejected", "workspace_error", "phase_error", "failed",
            "limit_reached", "review_inconclusive", "rework_limit_reached", "needs_input"}


def canonical_hash(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def attach(state, root):
    if not state["workspace_enabled"] or not state["base_commit"]:
        return None
    workspace = Workspace(Path(state["task"]["repository"]), root / "worktrees",
                          path=root / "worktrees" / state["run_id"], base_commit=state["base_commit"])
    return workspace


def approval_digest(state):
    evidence = {k: state.get(k) for k in
        ("task", "issue", "reviewed_commit", "review_report", "verified", "base_commit")}
    # 无人工补充的历史运行保留原摘要算法；新答复必须进入批准依据。
    if state.get("human_answers"):
        evidence["human_answers"] = state["human_answers"]
    if state.get("task_brief"):
        evidence["task_brief"] = state["task_brief"]
    if state.get("plan"):
        evidence["plan"] = state["plan"]
        evidence["plan_progress"] = state.get("plan_progress")
    if state.get("integrations"):
        evidence["integrations"] = state["integrations"]
    if (state.get("knowledge_snapshot") or {}).get("entries"):
        evidence["knowledge_snapshot"] = state["knowledge_snapshot"]
    # M5：审核依据和具体争议结论进入批准摘要；没有M5标记的旧运行保持原摘要算法。
    if state.get("review_schema") == 2:
        if state.get("review_basis"):
            evidence["review_basis"] = state["review_basis"]
        if state.get("disputes"):
            evidence["disputes"] = [
                {key: item.get(key) for key in ("id", "key", "commit", "outcome",
                                                 "decided_at", "reason")}
                for item in state["disputes"]]
        if state.get("rework_limit") is not None:
            evidence["rework_limit"] = state["rework_limit"]
            evidence["rework_extensions"] = state.get("rework_extensions") or []
    return canonical_hash(evidence)
