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
    return canonical_hash({k: state.get(k) for k in
        ("task", "issue", "reviewed_commit", "review_report", "verified", "base_commit")})
