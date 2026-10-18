"""单次交付的概览与导航；只解释已有证据，不产生运行或审批动作。"""

from html import escape
from pathlib import Path
import re

LABELS = {
    'published': '草稿 PR 已创建', 'awaiting_approval': '等待人工审批',
    'approved': '已批准，待创建 PR', 'running': '正在执行', 'paused': '已暂停',
    'failed': '执行失败', 'workspace_error': '工作区异常', 'phase_error': '阶段异常',
    'limit_reached': '达到限额', 'review_inconclusive': '审核未形成结论',
    'rework_limit_reached': '返工达到上限', 'needs_input': '需要澄清',
    'recovery_required': '需要核对现场', 'interrupted': '运行已中断',
    'rejected': '人工已拒绝', 'completed': '报告流程结束',
    'publishing': '正在创建 PR', 'publication_failed': 'PR 创建失败',
}


def delivery_steps(state, changes):
    """每个节点按自己的证据判断，后续状态不能把前面的缺失证据补成成功。"""
    report, checks = state.get('report') or {}, state.get('verified') or []
    review, approval, pull = (state.get(key) or {} for key in ('review_report', 'approval', 'pull_request'))
    checked = bool(checks) and all(c.get('exit_code') == 0 and not c.get('timed_out')
                                  and not c.get('error') for c in checks)
    check_failed = bool(checks) and not checked
    approved = approval.get('approved') is True
    stale = bool(approval.get('commit') and state.get('reviewed_commit')
                 and approval['commit'] != state['reviewed_commit'])
    steps = [
        ('需求', 'done' if state.get('issue') or (state.get('task') or {}).get('request') else 'pending', '工单或本地需求'),
        ('实现', 'done' if changes.get('files') and report.get('status') == 'implemented' else 'pending', '候选文件改动'),
        ('独立复核', 'done' if checked else 'failed' if check_failed else 'pending', f'{len(checks)} 项登记检查' if checks else '尚无检查记录'),
        ('Agent 审核', 'done' if review.get('verdict') == 'approved' else 'failed' if review.get('verdict') else 'pending',
         {'approved': '已通过', 'changes_requested': '要求返工', 'unable_to_review': '无法确认'}.get(review.get('verdict'), '尚无审核结论')),
        ('人工审批', 'failed' if stale or approval.get('approved') is False else 'done' if approved else 'pending',
         '批准版本已变化' if stale else '已批准' if approved else '已拒绝' if approval.get('approved') is False else '等待人工决定'),
        ('草稿 PR', 'done' if pull.get('url') else 'pending', '已创建 · 尚未合并' if pull.get('url') else '尚未创建'),
    ]
    return steps


def overview(state, changes, headline, action):
    status = str(state.get('status') or 'unknown')
    label = LABELS.get(status, '未知状态')
    tone = 'failed' if status in {'failed', 'workspace_error', 'phase_error', 'limit_reached',
        'publication_failed', 'rejected', 'review_inconclusive', 'rework_limit_reached'} else 'waiting'
    if status == 'published':
        tone = 'draft'
    repository = str((state.get('issue') or {}).get('repository') or '本地任务')
    rows = ''.join(
        f'<li class="check-row {kind}"><span class="check-mark" aria-hidden="true">'
        f'{"✓" if kind == "done" else "×" if kind == "failed" else "·"}</span>'
        f'<strong>{escape(name)}</strong><span class="check-detail">{escape(detail)}</span></li>'
        for name, kind, detail in delivery_steps(state, changes))
    request = str((state.get('task') or {}).get('request') or '')
    request_block = (f'<details class="request"><summary>原始需求</summary><p>{escape(request)}</p></details>'
                     if request else '')
    header = (f'<header class="overview" id="overview"><p class="repository">{escape(repository)}</p>'
            f'<h1>{escape(headline)}</h1><div class="run-meta"><span class="status {tone}">{escape(label)}</span>'
            f'<code>{escape(str(state.get("run_id") or ""))}</code><span>只读快照</span></div></header>')
    content = (
            f'<section class="handoff" aria-label="运行结果"><div class="handoff-heading"><strong>{escape(label)}</strong>'
            f'<code>{escape(str(state.get("stop_reason") or "未记录停止原因"))}</code></div>'
            f'<div class="action">{action or "<p>请结合实现结论与证据判断结果；流程结束不等于已经交付。</p>"}</div>'
            f'<ul class="check-list" aria-label="交付进度">{rows}</ul>{request_block}</section>')

    return header, content


def run_metadata(state):
    execution = (state.get('task') or {}).get('execution') or {}
    values = [('运行 ID', state.get('run_id')), ('阶段', state.get('phase')),
              ('执行环境', execution.get('backend', 'local')),
              ('候选提交', state.get('reviewed_commit')), ('基线提交', state.get('base_commit'))]
    return '<dl class="run-properties">' + ''.join(
        f'<dt>{escape(label)}</dt><dd><code title="{escape(str(value))}">'
        f'{escape(str(value)[:12] if label.endswith("提交") else str(value))}</code></dd>'
        for label, value in values if value is not None) + '</dl>'


def shell(title, body, hero, state):
    headings = []
    def anchor(match):
        identifier = f'evidence-{len(headings) + 1}'
        headings.append((identifier, match[1]))
        return f'<h2 id="{identifier}">{match[1]}</h2>'
    body = re.sub(r'<h2>([^<]+)</h2>', anchor, body)
    nav = '<a href="#overview">概览</a>' + ''.join(
        f'<a href="#{identifier}">{text}</a>' for identifier, text in headings)
    css = Path(__file__).with_name('report.css').read_text(encoding='utf-8')
    header, content = hero
    metadata = run_metadata(state)
    css += "\n" + "\n".join(
        f'body:has(#{identifier}:target) .evidence-tabs a[href="#{identifier}"] {{border-bottom:2px solid #fd8c73;font-weight:600}}'
        for identifier, _ in headings)
    return (f'<!DOCTYPE html>\n<html lang="zh-CN"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(title)}</title>'
            f'<style>{css}</style></head><body><a class="skip" href="#overview">跳到报告内容</a>'
            '<div class="site-header"><a class="wordmark" href="#overview">RepoPilot</a>'
            '<span class="header-divider">/</span><span>运行报告</span><span class="local-label">Local</span></div>'
            f'<main>{header}<nav class="evidence-tabs" aria-label="报告导航">{nav}</nav>'
            f'<div class="page-columns"><div class="report-content">{content}'
            f'<article>{body}</article></div><aside class="outline"><h2>运行信息</h2>{metadata}'
            '<div class="scope"><strong>交付边界</strong><p>人工批准后创建草稿 PR。</p>'
            '<p>合并与部署尚未接入。</p><p>这是已落盘的证据，不是实时监控，也不执行审批。</p></div></aside></div>'
            '<footer>RepoPilot · 本地运行证据<br>不含原始模型会话与账本；路径及常见凭证已脱敏。分享前请检查业务内容。</footer>'
            '</main></body></html>')


def diff_view(diff):
    lines = []
    for line in diff.splitlines():
        kind = ('addition' if line.startswith('+') else 'deletion' if line.startswith('-')
                else 'hunk' if line.startswith('@@') else 'context')
        lines.append(f'<span class="{kind}">{escape(line)}</span>')
    return '<pre class="diff">' + ''.join(lines) + '</pre>'
