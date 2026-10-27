"""按证据类别渲染报告片段；与页面布局、落盘读取分离。"""

from html import escape
import json
from repopilot.planning import planner
from .formatting import safe_url, redact, tail, table
from .layout import diff_view

def requirement(state, root):
    parts = []
    issue = state.get("issue") or {}
    if issue:
        repository = escape(str(issue.get("repository") or ""))
        parts.append("<h2>需求来源</h2>")
        parts.append(
            "<table><tbody>"
            f"<tr><th>仓库</th><td><code>{repository}</code></td></tr>"
            f"<tr><th>工单</th><td>#{escape(str(issue.get('number') or '-'))}</td></tr>"
            f"<tr><th>链接</th><td><a class='url' href='{escape(safe_url(issue.get('url')))}'>"
            f"{escape(str(issue.get('url') or ''))}</a></td></tr>"
            "</tbody></table>"
        )
        if state.get("base_commit"):
            parts.append(f"<p class='meta'>基线提交 <code>{escape(str(state['base_commit']))}</code></p>")

    return "\n".join(parts)


def metrics(state):
    parts = []
    control, edits = state.get("control") or {}, state.get("edits") or {}
    cards = [
        (str(control.get("model_calls", "-")), "模型调用"),
        (str(control.get("tool_calls", "-")), "工具调用"),
        (f"{edits.get('edits', '-')}/{edits.get('edit_limit', '-')}", "编辑次数"),
        (f"¥{control.get('accounted_cny', 0):.4f}" if isinstance(control.get("accounted_cny"), (int, float)) else "-",
         "估算费用"),
    ]
    if isinstance(state.get("active_seconds"), (int, float)):
        cards.append((f"{state['active_seconds']:.0f}s", "活跃时长"))
    parts.append('<h2>关键指标</h2><div class="grid">')
    for value, label in cards:
        parts.append(f'<div class="card"><b>{escape(value)}</b><span>{escape(label)}</span></div>')
    parts.append("</div>")

    return "\n".join(parts)


def implementation(report, root):
    parts = []
    if report:
        parts.append("<h2>实现报告</h2>")
        parts.append(f"<p>{escape(redact(str(report.get('summary') or ''), root))}</p>")
        evidence_rows = []
        for item in report.get("evidence") or []:
            if item.get("kind") == "check":
                detail = f"检查 {escape(str(item.get('name')))} · 退出码 <code>{escape(str(item.get('exit_code')))}</code>"
            else:
                detail = (f"<code>{escape(str(item.get('path')))}:{escape(str(item.get('start_line')))}</code>")
            evidence_rows.append([escape(str(item.get("kind"))), detail,
                                  escape(redact(str(item.get("observation") or ""), root))])
        parts.append(table(["类型", "位置", "观察"], evidence_rows))

    return "\n".join(parts)


def subtask_reports(state, root):
    """计划模式下列出每个子任务结果；最后一份实现报告不能代表全部子任务。"""
    results = (state.get("plan_progress") or {}).get("results") or []
    if not results or not state.get("plan"):
        return ""
    rows = []
    for item in results:
        commit = str(item.get("commit") or "")
        evidence = item.get("evidence") or []
        rows.append([escape(str(item.get("id"))), escape(redact(str(item.get("title") or ""), root)),
                     escape(str(item.get("status"))),
                     escape(redact(str(item.get("summary") or ""), root)),
                     escape(str(len(evidence))),
                     f"<code>{escape(commit[:10])}</code>" if commit else "-"])
    note = '<p class="note">上方“实现报告”只属于最后一个子任务；按子任务结果与提交如下。</p>'
    return "<h2>子任务实现报告</h2>" + note + table(
        ["子任务", "标题", "状态", "摘要", "证据数", "提交"], rows)


def human_requests(state, root):
    requests = state.get("human_requests") or []
    if not requests:
        return ""
    kinds = {"clarification": "需求澄清", "approval": "交付批准", "recovery": "现场恢复", "context_update": "资料依据更新"}
    statuses = {"pending": "等待答复", "answered": "已答复", "superseded": "版本已替代"}
    rows = []
    for item in requests:
        reply = item.get("reply") or {}
        label = kinds.get(item.get("kind"), str(item.get("kind")))
        if item.get("final"):
            label += "（最终缺口）"
        elif item.get("batch"):
            label += f"（第{item['batch']}批）"
        rows.append([escape(str(item.get("id", ""))),
            escape(label),
            escape(statuses.get(item.get("status"), str(item.get("status")))),
            escape(redact("\n".join(item.get("questions") or []), root)),
            escape(redact(str(reply.get("text") or ""), root))])
    return '<h2>人工请求</h2>' + table(["请求", "类型", "状态", "问题", "答复"], rows)


def plan(state, root):
    """展示任务说明、子任务计划、执行进度和提问批次；只读已有证据。"""
    brief = state.get("task_brief") or {}
    plan_data = state.get("plan") or {}
    progress = state.get("plan_progress") or {}
    batches = state.get("question_batches") or []
    mappings = state.get("subtask_mappings") or []
    if not brief and not plan_data and not batches:
        return ""
    parts = []
    if brief:
        parts.append("<h2>任务说明与计划</h2>")
        unresolved = brief.get("unresolved") or []
        rows = [["版本", f"r{brief.get('revision', 0)}"],
                ["目标", escape(redact(str(brief.get("goal") or ""), root))],
                ["验收", escape(redact("；".join(brief.get("acceptance") or []) or "尚未整理", root))],
                ["假设", escape(redact("；".join(brief.get("assumptions") or []) or "无", root))],
                ["范围外", escape(redact("；".join(brief.get("out_of_scope") or []) or "无", root))],
                ["未决", escape(redact("；".join(unresolved), root)) if unresolved else "无"]]
        parts.append("<table><tbody>" + "".join(
            f"<tr><th>{escape(str(label))}</th><td>{value}</td></tr>" for label, value in rows) + "</tbody></table>")
    if plan_data:
        status = planner.status(state)
        brief_revision = plan_data.get("brief_revision")
        current_revision = brief.get("revision")
        stale = current_revision is not None and brief_revision is not None and current_revision != brief_revision
        parts.append(f"<p class='meta'>计划 <code>{escape(str(plan_data.get('version')))}</code>"
                     f" · {escape(str(plan_data.get('split')))} · "
                     f"完成 {escape(str(status.get('completed', 0)))}/{escape(str(status.get('total', 0)))}"
                     f" · 任务说明 r{escape(str(brief_revision))}（当前 r{escape(str(current_revision))}）"
                     f" · {escape(str(plan_data.get('split_reason') or ''))}</p>")
        if stale:
            parts.append('<p class="bad">计划基于旧版任务说明；答复后应由程序重新规划，'
                         '未重规划前不能把旧计划当作当前验收。</p>')
        results = {item.get("id"): item for item in progress.get("results") or []}
        rows = []
        for subtask in plan_data.get("subtasks") or []:
            result = results.get(subtask.get("id")) or {}
            commit = str(result.get("commit") or "")
            outcome = {"completed": "已完成", "blocked": "阻塞", "failed": "失败",
                       "needs_clarification": "待澄清"}.get(result.get("status"), "待执行")
            outcome_cell = escape(outcome)
            if commit:
                outcome_cell += f" <code>{escape(commit[:10])}</code>"
            rows.append([escape(str(subtask.get("id"))),
                         escape(redact(str(subtask.get("title") or ""), root)),
                         escape("、".join(subtask.get("depends_on") or []) or "无"),
                         escape(redact("；".join(subtask.get("boundaries") or []), root)),
                         escape(redact("；".join(subtask.get("acceptance") or []), root)),
                         outcome_cell])
        parts.append(table(["子任务", "目标", "依赖", "修改边界", "验收", "进度"], rows))
    refreshes = state.get("plan_refresh_history") or []
    if refreshes:
        rows = []
        for item in refreshes:
            old_progress = item.get("old_progress") or {}
            completed = sum(1 for result in old_progress.get("results") or []
                            if result.get("status") == "completed")
            rows.append([escape(str(item.get("old_plan"))),
                         f"r{escape(str(item.get('old_brief_revision')))}",
                         f"r{escape(str(item.get('new_brief_revision')))}",
                         escape(str(completed)), escape(str(item.get("at") or ""))])
        parts.append('<h3>重规划记录</h3>' + table(
            ["旧计划", "旧任务说明", "新任务说明", "旧已完成子任务", "时间"], rows))
    if mappings:
        rows = [[escape(str(item.get("subtask"))), escape(str(item.get("status"))),
                 escape(str(item.get("kind") or "")),
                 f"<a class='url' href='{escape(safe_url((item.get('issue') or {}).get('url')))}'>"
                 f"{escape(str(((item.get('issue') or {}).get('url')) or '仅本地'))}</a>",
                 escape(redact(str(item.get("note") or ""), root))] for item in mappings]
        parts.append('<h3>子Issue映射</h3>' + table(["子任务", "状态", "类型", "外部工单", "说明"], rows))
    if batches:
        limit = state.get("question_limit") or 3
        rows = []
        for item in batches:
            delivery = (next((request.get("delivery") for request in state.get("human_requests") or []
                              if request.get("id") == item.get("request_id")), None) or {})
            rows.append([escape(str(item.get("batch"))), escape(str(item.get("request_id"))),
                         escape("已答复" if item.get("answered_at") else "等待答复"),
                         escape(str(delivery.get("status") or "未投递")),
                         escape(redact("\n".join(item.get("questions") or []), root))])
        parts.append(f'<h3>集中提问（{len(batches)}/{escape(str(limit))} 批）</h3>'
                     + table(["批次", "请求", "状态", "评论投递", "问题"], rows))
    return "\n".join(parts)


def materials(state, root):
    snapshot = state.get("knowledge_snapshot") or {}
    entries = snapshot.get("entries") or []
    decisions = state.get("decision_records") or []
    if not entries and not decisions:
        return ""
    rows = [[escape(str(item.get(key, ""))) for key in ("id", "kind", "title", "version", "path")]
            for item in entries]
    parts = ['<h2>固定资料依据</h2>', table(["资料", "类型", "标题", "版本", "原文位置"], rows)]
    history = state.get("knowledge_history") or {}
    if len(history) > 1:
        parts.append('<h3>资料清单历史</h3>' + table(["清单版本", "资料数"],
            [[escape(identifier), str(len(item.get("entries", [])))] for identifier, item in history.items()]))
    if decisions:
        parts.append('<h3>机器决定记录</h3>' + table(["记录", "阶段", "状态", "版本"],
            [[escape(str(item.get(key, ""))) for key in ("id", "phase", "status", "version")]
             for item in decisions]))
    return "\n".join(parts)


def candidate(changes, root):
    parts = []
    changed = changes.get("files") or []
    if changed:
        parts.append("<h2>候选改动</h2>")
        parts.append(
            f"<p class='meta'>相对基线 <code>{escape(str(changes.get('base_commit'))[:10])}</code>"
            f" · {changes.get('changed_files')} 个文件 · +{changes.get('additions')}/-{changes.get('deletions')}</p>"
        )
        parts.append('<div class="file-list">' + table(["文件", "状态", "增", "减"],
                           [[f"<code>{escape(str(item.get('path')))}</code>", escape(str(item.get("status"))),
                             escape(str(item.get("additions"))), escape(str(item.get("deletions")))]
                            for item in changed]) + "</div>")
        diff = redact(str(changes.get("diff") or ""), root)
        if diff.strip():
            truncation = "（已截断）" if changes.get("truncated") else ""
            parts.append(f"<details open><summary>差异{truncation}</summary>{diff_view(diff)}</details>")

    return "\n".join(parts)


def checks(verified, root):
    parts = ['<h2>独立复核</h2>', '<p class="note">编排层重新执行登记检查，以下为真实退出结果。</p>']
    if not verified:
        parts.append('<p class="note">没有可展示的记录。</p>')
    for item in verified:
        passed = item.get('exit_code') == 0 and not item.get('timed_out') and not item.get('error')
        label = '通过' if passed else '超时' if item.get('timed_out') else '未通过'
        kind, mark = ('done', '✓') if passed else ('failed', '×')
        output = tail(str(item.get('output_tail') or item.get('error') or '没有输出记录'))
        parts.append(f'<details class="check-result {kind}" open><summary>'
                     f'<span class="check-mark">{mark}</span> {escape(str(item.get("name")))}'
                     f'<span class="result-label">{label} · exit {escape(str(item.get("exit_code")))}</span>'
                     f'</summary><pre>{escape(redact(output, root))}</pre></details>')
    return "\n".join(parts)


def reviews(history, review, root):
    parts = []
    if history or review.get("verdict"):
        parts.append("<h2>审核与返工</h2>")
        review_rows = []
        for round_number, item in enumerate(history, 1):
            findings = item.get("findings") or []
            review_rows.append([
                str(round_number),
                f"<code>{escape(str(item.get('commit'))[:10])}</code>",
                f"<span class='tag' title='{escape(str(item.get('verdict')))}'>"
                + {"approved": "已通过", "changes_requested": "要求返工", "unable_to_review": "无法确认"}.get(item.get("verdict"), "未记录") + "</span>",
                escape(str(len(findings))),
                '<details class="review-summary"><summary>' + escape(redact(str(item.get("summary") or ""), root))[:65]
                + '…</summary><p>' + escape(redact(str(item.get("summary") or ""), root)) + "</p></details>",
            ])
        parts.append(table(["轮次", "审核锚点", "结论", "发现数", "说明"], review_rows, nowrap_first=True))
        findings = (review.get("findings") or [])
        if not findings and history:
            # 审核最终通过时 review_report 没有发现，结论来自最后一轮之前的那一轮。
            findings = next((item.get("findings") or [] for item in reversed(history[:-1] or history)
                             if item.get("findings")), [])
        if findings:
            parts.append("<h3>历史审核发现</h3>" if review.get("verdict") == "approved" else "<h3>审核发现</h3>")
            if review.get("verdict") == "approved":
                parts.append('<p class="note">以下来自此前轮次；最后一轮审核结论为通过。</p>')
            parts.append(table(["严重程度", "位置", "观察", "建议"],
                               [[escape(str(item.get("severity"))),
                                 f"<code>{escape(str(item.get('path')))}:{escape(str(item.get('start_line')))}</code>",
                                 escape(redact(str(item.get("observation") or ""), root)),
                                 escape(redact(str(item.get("suggestion") or ""), root))] for item in findings]))

    return "\n".join(parts)


def decision(approval, root):
    parts = []
    if approval:
        parts.append("<h2>人工决定</h2>")
        parts.append(
            "<table><tbody>"
            f"<tr><th>决定</th><td>{'批准' if approval.get('approved') else '拒绝'}</td></tr>"
            f"<tr><th>绑定提交</th><td><code>{escape(str(approval.get('commit') or ''))}</code></td></tr>"
            f"<tr><th>数据摘要</th><td><code>{escape(str(approval.get('digest') or ''))}</code></td></tr>"
            f"<tr><th>理由</th><td>{escape(redact(str(approval.get('reason') or ''), root))}</td></tr>"
            f"<tr><th>时间</th><td>{escape(str(approval.get('at') or ''))}</td></tr>"
            "</tbody></table>"
        )

    return "\n".join(parts)


def remote(pull, publication, approval, root):
    parts = []
    if pull:
        parts.append("<h2>远端产物</h2>")
        parts.append(
            "<table><tbody>"
            f"<tr><th>草稿 PR</th><td><a class='url' href='{escape(safe_url(pull.get('url')))}'>"
            f"{escape(str(pull.get('url') or ''))}</a></td></tr>"
            f"<tr><th>PR 提交</th><td><code>{escape(str(pull.get('commit') or ''))}</code></td></tr>"
            f"<tr><th>时间</th><td>{escape(str(pull.get('at') or ''))}</td></tr>"
            "</tbody></table>"
        )
        if pull.get("commit") and approval.get("commit") and pull["commit"] != approval["commit"]:
            parts.append('<p class="bad">注意：PR 提交与获批提交不一致，需要人工核对。</p>')
    elif publication:
        parts.append("<h2>远端产物</h2>")
        parts.append("<p class='note'>发布记录存在，但没有 PR 信息。</p>"
                     f"<pre>{escape(redact(json.dumps(publication, ensure_ascii=False, indent=1), root))}</pre>")

    return "\n".join(parts)
