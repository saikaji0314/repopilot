"""审核依据的固定版本、失效与复用规则；不调用模型或修改代码。

一轮正式审核绑定同一份证据：候选提交、基线、独立检查、资料清单、任务说明版本、
计划版本和需求答复版本。任何一项变化都产生新的依据哈希，旧结论不能静默复用。
"""

from repopilot.orchestration.state import canonical_hash
from repopilot.storage.jobs import timestamp


def ensure(state) -> None:
    """旧运行没有M5字段时补默认值；不删除历史审核或争议。"""

    for key, default in (("review_rounds", []), ("disputes", []), ("review_reuse", []),
                         ("review_invalidations", []), ("review_pending", None),
                         ("review_draft", None), ("rework_extensions", []),
                         ("review_dispute_decisions", []), ("review_basis", None)):
        state.setdefault(key, default)
    if state.get("rework_limit") is None:
        state["rework_limit"] = int((state.get("settings") or {}).get("max_rework", 3))


def snapshot(state) -> dict:
    brief = state.get("task_brief") or {}
    plan = state.get("plan") or {}
    knowledge = state.get("knowledge_snapshot") or {}
    data = {
        "schema_version": 1,
        "run_id": state.get("run_id"),
        "commit": str(state.get("reviewed_commit") or ""),
        "base_commit": str(state.get("base_commit") or ""),
        "verified_sha256": canonical_hash(state.get("verified") or []),
        "knowledge_snapshot_id": knowledge.get("snapshot_id"),
        "brief_revision": brief.get("revision"),
        "brief_sha256": brief.get("sha256"),
        "plan_version": plan.get("version"),
        "requirements_revision": state.get("requirements_revision", 0),
        "rework": state.get("reworks", 0),
    }
    data["hash"] = canonical_hash(data)
    return data


def reusable_round(state, basis: dict):
    """同一提交、同一依据的已完成审核可直接复用，不重新调用模型。"""

    report = state.get("review_report") or {}
    stored = state.get("review_basis") or {}
    if (state.get("review_rounds") and report.get("verdict")
            and stored.get("hash") == basis.get("hash")
            and (report.get("basis") or {}).get("commit") == basis.get("commit")):
        return {"report": report, "round": state["review_rounds"][-1].get("round"),
                "commit": report.get("basis", {}).get("commit") or basis.get("commit")}
    for record in reversed(state.get("review_rounds") or []):
        saved = record.get("report") or {}
        if (record.get("complete") and record.get("commit") == basis.get("commit")
                and (record.get("basis") or {}).get("hash") == basis.get("hash")
                and saved.get("verdict")):
            return {"report": saved, "round": record.get("round"), "commit": record.get("commit")}
    return None


def advance(state, basis: dict) -> None:
    state["review_basis"] = basis


def invalidate(state, reason: str) -> bool:
    """需求、资料或检查依据变化后保留历史，但撤销当前结论与批准。"""

    changed = any((state.get("verified"), state.get("review_report"),
                   state.get("approval"), state.get("approval_digest")))
    if changed:
        state.setdefault("review_invalidations", []).append({
            "reason": reason, "at": timestamp(),
            "commit": state.get("reviewed_commit"),
            "review_rounds": len(state.get("history") or []),
            "basis_hash": (state.get("review_basis") or {}).get("hash"),
        })
    state["verified"] = []
    state["review_report"] = None
    state["approval"] = None
    state["review_basis"] = None
    state.pop("approval_digest", None)
    return changed
