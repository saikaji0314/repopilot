"""具体争议的记录、A／B／C 结论应用与版本边界。

只有两位审核者对同一发现给出有依据的相反判断才创建争议；同一提交、同一审核依据
中的同一发现最多启动一次 A／B＋C。旧提交／旧依据的结论只作历史，不会自动排除
新候选上的同名发现；需要重新核对或交人，不能把过期结论当成当前证据。
"""

from repopilot.storage.jobs import timestamp

from . import basis as basis_store
from . import merge
from .contracts import DisputePosition, DisputeRecord


def records(state) -> list[dict]:
    return list(state.get("disputes") or [])


def find(state, dispute_id: str) -> dict:
    for record in records(state):
        if record.get("id") == dispute_id:
            return record
    raise KeyError(dispute_id)


def for_key(state, key: str):
    """仅为历史查看保留；判断是否可复用必须使用[reuse_outcome]。"""

    return next((record for record in reversed(records(state)) if record.get("key") == key), None)


def for_candidate(state, key: str, basis: dict):
    """同提交且同实质依据才属于同一个可复用争议。

    旧记录没有 dispute_basis_hash 时，只在完整依据哈希也相同时回退；
    这与未决争议的恢复查找使用同一规则。
    """

    dispute_key = basis_store.dispute_hash(basis)
    for record in reversed(records(state)):
        if record.get("key") != key or record.get("commit") != str(basis.get("commit") or ""):
            continue
        stored = record.get("dispute_basis_hash")
        if stored:
            if stored == dispute_key:
                return record
        elif record.get("basis_hash") == basis.get("hash"):
            return record
    return None


def create(state, *, round_number: int, finding: merge.MergedFinding, positions: list[dict],
           basis: dict) -> dict:
    record = DisputeRecord(id=f"d-{len(records(state)) + 1}", key=finding.key,
        round=round_number, commit=str(state.get("reviewed_commit") or ""),
        basis_hash=basis["hash"], dispute_basis_hash=basis_store.dispute_hash(basis),
        finding=finding.model_dump(mode="json"),
        positions=[DisputePosition.model_validate(item) for item in positions],
        created_at=timestamp())
    state.setdefault("disputes", []).append(record.model_dump(mode="json"))
    return record.model_dump(mode="json")


def reuse_outcome(state, finding: merge.MergedFinding, basis: dict):
    """同提交、同实质依据的争议结论可以复用；返工计数变化不算新依据。"""

    record = for_candidate(state, finding.key, basis)
    if record is None:
        return None
    outcome = record.get("outcome")
    if outcome in {"confirmed", "human_confirmed", "reused_confirmed"}:
        return "confirmed", record
    if outcome in {"refuted", "human_refuted", "reused_refuted"}:
        return "refuted", record
    return "pending", record


def payload(dispute: dict, shared: dict, *, side: str | None = None,
            reports: dict | None = None) -> dict:
    data = dict(shared)
    data["dispute"] = {
        "id": dispute["id"],
        "commit": dispute["commit"],
        "finding": dispute["finding"],
        "positions": dispute["positions"],
        "note": "只核对这一条具体争议；不重做整份审核，也不修改代码或验收。",
    }
    if side is not None:
        data["side"] = side
        data["instruction"] = ("独立核对双方证据，不假设另一路会同意你；"
                               "引用文件时给出实际读到的相对路径与起始行号。")
    if reports is not None:
        data["reports"] = reports
        data["instruction"] = "比较 A／B 两份独立复核的证据，不按票数判断。"
    return data


def apply_reports(state, dispute_id: str, *, a: dict, b: dict, c: dict) -> str:
    """记录 A／B／C 报告并按 C 的结论更新发现；返回 confirmed／refuted／undetermined。"""

    record = find(state, dispute_id)
    outcome = {"confirmed": "confirmed", "refuted": "refuted",
               "undetermined": "undetermined"}[c["outcome"]]
    record.update(a=a, b=b, c=c, outcome=outcome, decided_at=timestamp(),
                  reason=str(c.get("reason") or c.get("summary") or "")[:4000])
    _apply(state, record, outcome)
    return outcome


def apply_human(state, dispute_id: str, *, decision: str, reason: str) -> str:
    """负责人对无法判断的争议给出明确判断；不改需求、不代替交付批准。"""

    if decision not in {"confirmed", "refuted"}:
        raise ValueError("dispute decision must be confirmed or refuted")
    record = find(state, dispute_id)
    if record.get("outcome") not in {"undetermined", "pending"}:
        raise ValueError("dispute_already_decided")
    outcome = "human_confirmed" if decision == "confirmed" else "human_refuted"
    record.update(outcome=outcome, reason=str(reason)[:4000], decided_at=timestamp(),
                  human={"decision": decision, "reason": reason, "at": timestamp(),
                         "actor": "local-operator"})
    _apply(state, record, decision)
    return outcome


def apply_reuse(state, dispute_id: str, *, outcome: str, reason: str) -> str:
    record = find(state, dispute_id)
    mapped = "reused_confirmed" if outcome == "confirmed" else "reused_refuted"
    record.update(outcome=mapped, reason=str(reason)[:4000], decided_at=timestamp())
    _apply(state, record, outcome)
    return mapped


def _apply(state, record: dict, outcome: str) -> None:
    status = {"confirmed": "open", "refuted": "invalid", "undetermined": "disputed"}[outcome]
    resolution = {"dispute": record["id"], "outcome": record.get("outcome"),
                  "reason": record.get("reason")}
    _update_finding(state, record["finding"]["id"],
                    key=record["finding"].get("key"), status=status, resolution=resolution)
    _sync_dispute(state, record)
    draft = state.get("review_draft")
    if draft:
        draft["verdict"] = merge.verdict_from(
            {name: value.get("verdict") for name, value in draft["perspectives"].items()},
            draft["findings"])
        return
    report = state.get("review_report") or {}
    if report.get("perspectives"):
        report["verdict"] = merge.verdict_from(
            {name: value.get("verdict") for name, value in report["perspectives"].items()},
            report.get("findings") or [])
        _sync_verdict(state, report["verdict"])


def _current_containers(state):
    draft = state.get("review_draft")
    if draft:
        # 一轮审核完成前，草稿是唯一权威记录；不能把旧轮次的同ID发现一起改写。
        return [draft]
    containers = []
    report = state.get("review_report")
    if report:
        containers.append(report)
    if state.get("review_rounds"):
        containers.append(state["review_rounds"][-1])
    if state.get("history"):
        containers.append(state["history"][-1])
    return containers


def _update_finding(state, finding_id: str, *, key: str | None = None, status: str,
                    resolution: dict | None = None) -> None:
    for container in _current_containers(state):
        for holder in (container, container.get("report") or {}):
            for item in holder.get("findings") or []:
                if item.get("id") != finding_id and (key is None or item.get("key") != key):
                    continue
                item["status"] = status
                item["blocking"] = (item.get("severity") in {"blocker", "major"}
                                    and status not in {"closed", "invalid"})
                if resolution is not None:
                    item["resolution"] = resolution


def _sync_verdict(state, verdict: str) -> None:
    for container in _current_containers(state):
        container["verdict"] = verdict
        holder = container.get("report")
        if isinstance(holder, dict):
            holder["verdict"] = verdict


def _sync_dispute(state, record: dict) -> None:
    """报告里保存的是争议快照；人工判断后同步同一ID，避免批准核对看到旧结论。"""

    for container in _current_containers(state):
        for holder in (container, container.get("report") or {}):
            for index, item in enumerate(holder.get("disputes") or []):
                if not isinstance(item, dict):
                    continue
                if item.get("id") == record.get("id"):
                    holder["disputes"][index] = {
                        **item, "outcome": record.get("outcome"),
                        "reason": record.get("reason"),
                        "decided_at": record.get("decided_at"),
                        "human": record.get("human")}
