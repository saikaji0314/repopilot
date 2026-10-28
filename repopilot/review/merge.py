"""审核报告的合并、去重、上一轮发现处理与阻断判定；纯函数，不调用模型。

规则边界：
- 同一问题按“依据位置＋观察文本相似度”合并，保留两个视角的原始措辞；
- 只有 blocker／major 且证据不是“信息不足”的发现能阻断；minor 只作建议；
- 复审时必须逐条回应上一轮固定ID；一方坚持存在、另一方有依据地排除才构成争议；
- 两人一致不代表有证据：都只写信息不足时仍不能关闭发现。
"""

from hashlib import sha256
import json
import re

from repopilot.agents.reports import Finding, ReviewReport

from .contracts import PERSPECTIVES, FindingSource, MergedFinding

SEVERITY_RANK = {"blocker": 3, "major": 2, "minor": 1}
EVIDENCE_RANK = {"reproduced": 3, "source_inference": 2, "insufficient_information": 1}
FIRM_EVIDENCE = {"reproduced", "source_inference"}


def normalize(text) -> str:
    return " ".join(str(text or "").split()).casefold()


def _tokens(text: str) -> set[str]:
    """中文按双字片段、拉丁按单词切分；只用于近似去重，不代替语义判断。"""

    value = normalize(text)
    cjk = "".join(re.findall(r"[\u4e00-\u9fff]", value))
    latin = re.findall(r"[a-z0-9_]{2,}", value)
    grams = [cjk[index:index + 2] for index in range(max(0, len(cjk) - 1))]
    if len(cjk) == 1:
        grams.append(cjk)
    return set(grams) | set(latin)


def similarity(left: str, right: str) -> float:
    a, b = _tokens(left), _tokens(right)
    if not a or not b:
        return 1.0 if normalize(left) == normalize(right) else 0.0
    return len(a & b) / len(a | b)


def _location(finding: Finding) -> list:
    if finding.basis == "code":
        # 行号按三行一桶，允许两位审核者指向相邻行。
        return ["code", finding.path.casefold(), (finding.start_line - 1) // 3]
    return [finding.basis, normalize(finding.reference)]


def same_issue(left: Finding, right: Finding) -> bool:
    if _location(left) != _location(right):
        return False
    a, b = normalize(left.observation), normalize(right.observation)
    if not a or not b:
        return a == b
    if a in b or b in a:
        return True
    return similarity(a, b) >= 0.5


def _key(finding: Finding) -> str:
    payload = json.dumps([_location(finding), normalize(finding.observation)],
                         ensure_ascii=False, sort_keys=True)
    return sha256(payload.encode("utf-8")).hexdigest()[:16]


def _join(values, limit=1000):
    result = []
    for value in values:
        text = " ".join(str(value or "").split())
        if text and text not in result:
            result.append(text)
    return "；".join(result)[:limit]


def _initial_status(severity: str, evidence_status: str) -> str:
    if severity == "minor":
        return "open"
    if evidence_status == "insufficient_information":
        return "unverified"
    return "open"


def merge_findings(round_number: int, reports: dict[str, ReviewReport]) -> list[MergedFinding]:
    """合并两个视角的发现：同一问题去重，互补问题保留，来源都可回查。"""

    clusters: list[dict] = []
    for perspective in PERSPECTIVES:
        report = reports.get(perspective)
        if report is None:
            continue
        for index, finding in enumerate(report.findings):
            for cluster in clusters:
                if same_issue(cluster["representative"], finding):
                    cluster["items"].append((perspective, index, finding))
                    break
            else:
                clusters.append({"representative": finding,
                                 "items": [(perspective, index, finding)]})
    merged: list[MergedFinding] = []
    for number, cluster in enumerate(clusters, 1):
        items = cluster["items"]
        findings = [item[2] for item in items]
        severity = max((item.severity for item in findings), key=lambda value: SEVERITY_RANK[value])
        evidence_status = max((item.evidence_status for item in findings),
                              key=lambda value: EVIDENCE_RANK[value])
        representative = next(item for item in findings if item.severity == severity)
        status = _initial_status(severity, evidence_status)
        merged.append(MergedFinding(
            id=f"r{round_number}-f{number}", key=_key(representative), round=round_number,
            basis=representative.basis, path=representative.path,
            start_line=representative.start_line, reference=representative.reference,
            severity=severity, evidence_status=evidence_status,
            requirement=_join(item.requirement for item in findings),
            trigger=_join(item.trigger for item in findings),
            impact=_join(item.impact for item in findings),
            observation=representative.observation, suggestion=representative.suggestion,
            sources=[FindingSource(perspective=perspective, index=index, severity=item.severity,
                                   evidence_status=item.evidence_status, observation=item.observation,
                                   suggestion=item.suggestion)
                     for perspective, index, item in items],
            # minor 可以记录，但不能阻断；信息不足的严重发现不能批准，也不能直接返工掉。
            blocking=severity in {"blocker", "major"},
            status=status))
    return merged


def _position(report: ReviewReport | None, finding_id: str):
    if report is None:
        return {"status": "missing", "evidence_status": "insufficient_information",
                "observation": "该视角没有提交本轮审核报告"}
    for response in report.responses:
        if response.finding_id == finding_id:
            return {"status": response.status, "evidence_status": response.evidence_status,
                    "observation": response.observation}
    return {"status": "missing", "evidence_status": "insufficient_information",
            "observation": "复审报告没有回应这条发现"}


def _positions(reports: dict[str, ReviewReport], finding_id: str):
    return [{"perspective": perspective, **_position(reports.get(perspective), finding_id)}
            for perspective in PERSPECTIVES]


def _firm(position) -> bool:
    return position["evidence_status"] in FIRM_EVIDENCE


def resolve_previous(round_number: int, previous: list[MergedFinding],
                     reports: dict[str, ReviewReport], new_findings: list[MergedFinding]):
    """按复审回应更新上一轮发现；返回 (更新后的发现, 新争议, 被重申而不再新建的ID)。

    关闭旧发现需要两个视角都给出可核对的关闭意见；漏答、unable_to_verify 或
    evidence_status=insufficient_information 都只能保留为待补证据。真实冲突优先于
    “重报旧发现”的去重分支，避免审核者多列一条 finding 就吞掉相反证据。
    """

    updated: list[MergedFinding] = []
    conflicts: list[tuple[MergedFinding, list[dict]]] = []
    reasserted: list[str] = []
    for finding in previous:
        if finding.status in {"closed", "invalid"}:
            updated.append(finding)
            continue
        positions = _positions(reports, finding.id)
        items = positions
        still = [item for item in items if item["status"] == "still_open"]
        closing = [item for item in items if item["status"] in {"resolved", "invalid"}]
        firm_still = [item for item in still if _firm(item)]
        firm_clearing = [item for item in closing if _firm(item)]
        reassertion = next((new for new in new_findings
                            if _same_reported(finding, new)), None)
        resolution = {"round": round_number, "responses": items}
        if (finding.severity in {"blocker", "major"}
                and firm_still and firm_clearing):
            # 真实冲突优先于去重，但重复 finding 仍要并回原ID，不能让同一问题出现两次。
            status, resolution["conflict"] = "disputed", True
            if reassertion is not None:
                reasserted.append(finding.id)
                new_findings.remove(reassertion)
                resolution["reasserted"] = True
        elif reassertion is not None:
            reasserted.append(finding.id)
            new_findings.remove(reassertion)
            resolution["reasserted"] = True
            status = "open"
        elif still:
            status = "open"
        elif (len(closing) == len(items) and len(items) > 0
              and all(_firm(item) for item in closing)):
            status = "invalid" if all(item["status"] == "invalid" for item in closing) else "closed"
        else:
            # 一方漏答、明确无法核验或只给信息不足时，不能据此关闭或批准。
            status = "unverified" if finding.severity in {"blocker", "major"} else "open"
            resolution["evidence_gap"] = True
            resolution["incomplete"] = any(
                item["status"] in {"missing", "unable_to_verify"}
                or item["evidence_status"] == "insufficient_information"
                for item in items)
        resolution["status"] = status
        updated_finding = finding.model_copy(update={
            "status": status,
            "blocking": finding.severity in {"blocker", "major"} and status not in {"closed", "invalid"},
            "resolution": resolution})
        updated.append(updated_finding)
        if status == "disputed":
            conflicts.append((updated_finding, items))
    return updated, conflicts, reasserted


def _same_reported(previous: MergedFinding, new: MergedFinding) -> bool:
    """复审把旧问题当新发现重复报告时，按同一依据位置合并回原ID。"""

    if previous.basis != new.basis:
        return False
    if previous.basis == "code":
        if previous.path != new.path:
            return False
        if previous.start_line is None or new.start_line is None:
            return previous.start_line == new.start_line
        if abs(previous.start_line - new.start_line) > 2:
            return False
    elif normalize(previous.reference) != normalize(new.reference):
        return False
    return similarity(previous.observation, new.observation) >= 0.5


def verdict_from(perspective_verdicts: dict[str, str], findings: list[dict]) -> str:
    """按视角结论和程序侧发现状态计算合并结论；新旧记录都走同一规则。"""

    if any(perspective_verdicts.get(name) is None for name in PERSPECTIVES):
        return "unable_to_review"
    if any(verdict == "unable_to_review" for verdict in perspective_verdicts.values()):
        return "unable_to_review"
    if any(finding.get("status") == "unverified" for finding in findings):
        return "unable_to_review"
    if any(finding.get("status") == "disputed" for finding in findings):
        return "undetermined"
    if any(finding.get("blocking") and finding.get("status") == "open" for finding in findings):
        return "changes_requested"
    return "approved"


def combine_verdict(reports: dict[str, ReviewReport], findings: list[MergedFinding]) -> str:
    """把两份报告变成程序结论；不能仅凭“两人都通过”放行信息不足或无证据的关闭。"""

    if any(reports.get(name) is None for name in PERSPECTIVES):
        return "unable_to_review"
    return verdict_from({name: report.verdict for name, report in reports.items()
                         if report is not None},
                        [finding.model_dump(mode="json") for finding in findings])


def combine_summary(reports: dict[str, ReviewReport], findings: list[MergedFinding],
                    verdict: str) -> str:
    labels = {"correctness": "正确性", "context": "上下文"}
    parts = [f"{labels[name]}：{report.summary}" for name, report in reports.items()
             if report is not None]
    blocking = [finding for finding in findings
                if finding.blocking and finding.status in {"open", "unverified", "disputed"}]
    advisory = [finding for finding in findings if not finding.blocking and finding.status == "open"]
    if blocking:
        parts.append(f"阻断发现 {len(blocking)} 条")
    if advisory:
        parts.append(f"非阻断建议 {len(advisory)} 条")
    if verdict == "approved":
        parts.append("两视角均未留下需修复的阻断问题")
    return "；".join(parts)[:2000]


def response_questions(reports: dict[str, ReviewReport]) -> list[str]:
    questions = []
    for report in reports.values():
        for item in (report.questions if report is not None else []):
            text = " ".join(str(item or "").split())
            if text and text not in questions:
                questions.append(text)
    return questions[:10]


def finding_view(finding: MergedFinding) -> dict:
    return finding.model_dump(mode="json")


def open_findings(findings: list[MergedFinding]) -> list[MergedFinding]:
    return [finding for finding in findings
            if finding.blocking and finding.status in {"open", "unverified", "disputed"}]


def approval_problems(state) -> list[str]:
    """审批前核对程序侧审核证据；旧运行没有M5记录时保持原摘要兼容。"""

    if not state.get("review_rounds"):
        return []
    report = state.get("review_report") or {}
    problems = []
    if report.get("verdict") != "approved":
        problems.append("review_not_approved")
    perspectives = report.get("perspectives") or {}
    if any(name not in perspectives for name in PERSPECTIVES):
        problems.append("dual_review_incomplete")
    findings = report.get("findings") or []
    if any(item.get("blocking") and item.get("status") in {"open", "unverified", "disputed"}
           for item in findings):
        problems.append("blocking_findings_unresolved")
    if report.get("disputes") and any(item.get("outcome") in {"pending", "undetermined"}
                                      for item in report["disputes"]):
        problems.append("review_dispute_unresolved")
    return problems
