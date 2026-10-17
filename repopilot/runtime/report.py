"""把一次运行的状态与本地证据渲染成单文件、可离线打开的静态 HTML 报告。

报告只读取已经落盘的状态与产物，不调用模型或 GitHub，也不写入任何新证据。
展示前会脱敏本机绝对路径与凭证；原始事件、账本与模型会话不进入报告。
"""

from html import escape
import json
from pathlib import Path
import re

DOCUMENT = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root {{ --fg:#1b1f24; --muted:#5b6573; --line:#d8dee6; --bg:#ffffff; --soft:#f6f8fa;
         --ok:#1a7f37; --warn:#9a6700; --bad:#b42318; --accent:#0b5cad; }}
* {{ box-sizing: border-box; }}
body {{ margin:0; padding:32px 20px 64px; background:var(--bg); color:var(--fg);
        font:15px/1.65 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif; }}
main {{ max-width:1040px; margin:0 auto; }}
h1 {{ font-size:26px; margin:0 0 6px; }}
h2 {{ font-size:19px; margin:36px 0 12px; padding-bottom:6px; border-bottom:1px solid var(--line); }}
h3 {{ font-size:16px; margin:20px 0 8px; }}
p {{ margin:8px 0; }}
.lead {{ color:var(--muted); margin:0 0 4px; }}
.meta {{ color:var(--muted); font-size:13px; }}
table {{ width:100%; border-collapse:collapse; margin:8px 0 4px; font-size:14px; }}
th, td {{ text-align:left; padding:8px 10px; border-bottom:1px solid var(--line); vertical-align:top; }}
th {{ color:var(--muted); font-weight:600; background:var(--soft); white-space:nowrap; }}
table.nowrap-first {{ table-layout:fixed; }}
table.nowrap-first th:nth-child(1) {{ width:6%; min-width:3.6em; }}
table.nowrap-first th:nth-child(2) {{ width:15%; }}
table.nowrap-first th:nth-child(3) {{ width:19%; }}
table.nowrap-first th:nth-child(4) {{ width:9%; min-width:4.6em; }}
table.nowrap-first th:nth-child(5) {{ width:51%; }}
table.nowrap-first th:first-child, table.nowrap-first td.n,
table.nowrap-first th:nth-child(4), table.nowrap-first td:nth-child(4) {{ white-space:nowrap; }}
code, .mono {{ font-family:Consolas,"Cascadia Mono",monospace; font-size:13px; }}
pre {{ background:var(--soft); border:1px solid var(--line); border-radius:6px; padding:12px;
       overflow-x:auto; font-size:13px; line-height:1.5; margin:8px 0;
       white-space:pre-wrap; word-break:break-word; }}
.tag {{ display:inline-block; padding:1px 8px; border-radius:999px; font-size:12px;
        border:1px solid var(--line); background:var(--soft); }}
.ok {{ color:var(--ok); }} .warn {{ color:var(--warn); }} .bad {{ color:var(--bad); }}
.url {{ color:var(--accent); word-break:break-all; }}
.note {{ border-left:3px solid var(--line); padding:2px 0 2px 12px; color:var(--muted); margin:10px 0; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:10px; margin:10px 0; }}
.card {{ border:1px solid var(--line); border-radius:6px; padding:10px 12px; }}
.card b {{ display:block; font-size:22px; font-weight:600; }}
.card span {{ color:var(--muted); font-size:13px; }}
</style>
</head>
<body><main>
{body}
<p class="meta" style="margin-top:40px">本报告由 <code>repopilot report</code> 从本地运行证据生成；
不含原始模型会话、账本与凭证，本机绝对路径已脱敏。</p>
</main></body>
</html>
"""


SECRET_NAMES = ("API_KEY", "DS_API_KEY", "GITHUB_TOKEN", "GH_TOKEN")

SECRET_RE = re.compile("|".join(
    [re.escape(name) for name in SECRET_NAMES] + [
        r"sk-[A-Za-z0-9_\-]{12,}",
        r"ghp_[A-Za-z0-9]{20,}",
        r"github_pat_[A-Za-z0-9_]{20,}",
        r"(?i:authorization\s*:\s*bearer\s+\S+)",
    ]))


def redact(text: str, root: Path | str | None = None) -> str:
    """遮蔽本机绝对路径与常见凭证。

    只做字符串层面替换：能挡住报告里最常见的路径与令牌形态，
    但不是通用敏感信息识别系统。
    """

    value = str(text if text is not None else "")
    markers = [str(marker) for marker in (root, Path.home() if root is None else None) if marker]
    for marker in markers:
        for variant in {marker, marker.replace("\\", "/")}:
            value = value.replace(variant, "<本机路径>")
    # 凭证只做一次整体替换：逐个替换会让标记本身被再次命中，产生嵌套标记。
    return SECRET_RE.sub("<凭证已脱敏>", value)


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def tail(output: str, lines: int = 12, limit: int = 1600) -> str:
    text = "\n".join((output or "").strip().splitlines()[-lines:])
    return text[:limit]


def status_class(status: str | None) -> str:
    if status in {"published", "approved", "completed", "ok"}:
        return "ok"
    if status in {"awaiting_approval", "paused", "needs_input"}:
        return "warn"
    if status in {"failed", "rejected", "workspace_error", "phase_error", "limit_reached"}:
        return "bad"
    return ""


def table(headers, rows, nowrap_first: bool = False) -> str:
    """渲染表格；短列（轮次、状态、时长）标记为不换行，避免一个字一行。"""

    if not rows:
        return '<p class="note">没有可展示的记录。</p>'
    head = "".join(f"<th>{escape(str(item))}</th>" for item in headers)
    cells = []
    for row in rows:
        rendered = [f'<td class="n">{cell}</td>' if nowrap_first and index == 0 else f"<td>{cell}</td>"
                    for index, cell in enumerate(row)]
        cells.append("<tr>" + "".join(rendered) + "</tr>")
    return f"<table{' class=\"nowrap-first\"' if nowrap_first else ''}><thead><tr>{head}</tr></thead><tbody>{''.join(cells)}</tbody></table>"


def timeline_events(events: list[dict], limit: int = 24) -> list[dict]:
    """挑出能说明“程序在做什么”的关键事件，跳过每次工具调用的噪声。"""

    interesting = {"workspace_ready", "verify_finished", "review_started", "review_finished",
                   "rework_started", "rework_finished", "rework_limit_reached", "review_skipped",
                   "run_error", "run_started", "run_finished", "model_failed", "phase_changed"}
    picked = [item for item in events if item.get("event") in interesting]
    return picked[-limit:]


def timeline_rows(events: list[dict], root: Path, limit: int = 24) -> list[list[str]]:
    """按时间顺序编号审核轮次与返工次数。

    审核事件里的 attempt 是审核轮次，返工事件里是返工次数，语义不同，
    因此各自独立计数，不直接复用字段值。
    """

    rows, reviews, reworks = [], 0, 0
    for event in timeline_events(events, limit):
        name = str(event.get("event"))
        moment = str(event.get("time") or "")[:19].replace("T", " ")
        if name == "review_started":
            reviews += 1
            text = f"开始审核第 {reviews} 轮，锚点提交 <code>{escape(str(event.get('commit'))[:10])}</code>"
        elif name == "review_finished":
            text = f"审核第 {reviews} 轮结论：{event.get('verdict')}（{event.get('findings', 0)} 条发现）"
        elif name == "verify_finished":
            text = f"编排层独立复核 {event.get('name')}：退出码 {event.get('exit_code')}"
        elif name == "rework_started":
            reworks += 1
            text = f"进入第 {reworks} 次返工（{event.get('findings')} 条审核意见）"
        elif name == "rework_finished":
            text = f"第 {reworks} 次返工结束，实现方自评 {event.get('status')}"
        elif name == "rework_limit_reached":
            text = f"返工达到上限（{event.get('limit')}），转人工"
        elif name == "review_skipped":
            text = f"跳过审核：{event.get('reason')}"
        elif name == "workspace_ready":
            text = "创建独立工作区，基线提交 " + f"<code>{escape(str(event.get('base_commit'))[:10])}</code>"
        elif name in {"run_error", "model_failed"}:
            text = f"{name}：{event.get('error_type') or ''} {escape(str(event.get('phase') or ''))}"
        else:
            text = name
        rows.append([escape(moment), f"<code>{escape(name)}</code>", redact(text, root)])
    return rows


def load_meta(run_dir: Path) -> dict:
    """可选的展示元数据；本地任务没有 Issue 时用它给出可读标题与说明。"""

    data = load_json(run_dir / "meta.json")
    return data if isinstance(data, dict) else {}


def next_action(state: dict) -> str:
    """用一句话说明这次运行为什么停在这里、人需要做什么。"""

    status = state.get("status")
    run_id = str(state.get("run_id") or "")
    commit = str(state.get("reviewed_commit") or "")
    safe_run, safe_commit = escape(run_id), escape(commit)
    if status == "awaiting_approval":
        handle = f"<code>approve {safe_run} --commit {safe_commit} --reason \"已核对差异与检查\"</code>"
        return f'<p class="note">等待人工审批：核对上方候选改动与独立复核后用 {handle} 批准，批准本身不推送。</p>'
    if status == "approved":
        return (f'<p class="note">已批准提交 <code>{safe_commit}</code>，'
                f'下一步用 <code>publish {safe_run}</code> 创建草稿 PR。</p>')
    if status == "published":
        return '<p class="note">已创建草稿 PR，尚未合并；合并与发布仍需单独决定。</p>'
    if status == "rework_limit_reached":
        return '<p class="note">返工达到上限，需要人工判断是继续、改需求还是放弃。</p>'
    if status == "review_inconclusive":
        return '<p class="note">审核无法判断，需要人工核对独立复核结果与候选改动。</p>'
    if status == "needs_input":
        return '<p class="note">实现方声明需要澄清，需要补充需求后再继续。</p>'
    if status in {"failed", "workspace_error", "phase_error", "limit_reached"}:
        return '<p class="note">运行未完成，需要查看失败原因后决定 retry 或调整配置。</p>'
    return ""


def render(state: dict, run_dir: Path, events: list[dict], root: Path) -> str:
    run_id = state.get("run_id") or run_dir.name
    issue = state.get("issue") or {}
    status = state.get("status")
    review = state.get("review_report") or {}
    control = state.get("control") or {}
    edits = state.get("edits") or {}
    approval = state.get("approval") or {}
    pull = state.get("pull_request") or {}
    publication = state.get("publication") or {}
    report = state.get("report") or {}
    verified = state.get("verified") or []
    history = state.get("history") or []
    changes = load_json(run_dir / "changes.json") or {}
    meta = load_meta(run_dir)

    headline = str((issue.get("title") if issue else None) or meta.get("title") or run_id)
    title = f"RepoPilot 运行报告 · {headline}"
    parts = [
        '<p class="lead">一键复盘一次工单交付：需求、改动、独立检查、审核返工、人工决定与远端产物。</p>',
        f"<h1>{escape(headline)}</h1>",
        f'<p class="meta">运行 <code>{escape(str(run_id))}</code>'
        f' · 状态 <span class="tag {status_class(status)}">{escape(str(status))}</span>'
        f' · 阶段 {escape(str(state.get("phase") or "-"))}'
        f' · 停止原因 <code>{escape(str(state.get("stop_reason") or "-"))}</code></p>',
    ]

    action = next_action(state)
    if action:
        parts.append(action)

    if meta.get("description"):
        parts.append(f"<p>{escape(redact(str(meta['description']), root))}</p>")

    if issue:
        repository = escape(str(issue.get("repository") or ""))
        parts.append("<h2>需求来源</h2>")
        parts.append(
            "<table><tbody>"
            f"<tr><th>仓库</th><td><code>{repository}</code></td></tr>"
            f"<tr><th>工单</th><td>#{escape(str(issue.get('number') or '-'))}</td></tr>"
            f"<tr><th>链接</th><td><a class='url' href='{escape(str(issue.get('url') or '#'))}'>"
            f"{escape(str(issue.get('url') or ''))}</a></td></tr>"
            "</tbody></table>"
        )
        if state.get("base_commit"):
            parts.append(f"<p class='meta'>基线提交 <code>{escape(str(state['base_commit']))}</code></p>")

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

    parts.append("<h2>阶段时间线</h2>")
    parts.append(table(["时间（UTC）", "事件", "说明"], timeline_rows(events, root)))

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

    changed = changes.get("files") or []
    if changed:
        parts.append("<h2>候选改动</h2>")
        parts.append(
            f"<p class='meta'>相对基线 <code>{escape(str(changes.get('base_commit'))[:10])}</code>"
            f" · {changes.get('changed_files')} 个文件 · +{changes.get('additions')}/-{changes.get('deletions')}</p>"
        )
        parts.append(table(["文件", "状态", "增", "减"],
                           [[f"<code>{escape(str(item.get('path')))}</code>", escape(str(item.get("status"))),
                             escape(str(item.get("additions"))), escape(str(item.get("deletions")))]
                            for item in changed]))
        diff = redact(str(changes.get("diff") or ""), root)
        if diff.strip():
            truncation = "（已截断）" if changes.get("truncated") else ""
            parts.append(f"<h3>差异{truncation}</h3><pre>{escape(diff)}</pre>")

    parts.append("<h2>独立复核</h2>")
    parts.append('<p class="note">复核由编排层执行，不采信实现方的自查结论。</p>')
    verified_rows = []
    for item in verified:
        output = escape(redact(tail(str(item.get("output_tail") or "")), root))
        verified_rows.append([escape(str(item.get("name"))), escape(str(item.get("exit_code"))),
                              escape(str(item.get("timed_out"))), f"<pre style='margin:0'>{output}</pre>"])
    parts.append(table(["检查", "退出码", "超时", "输出尾部"], verified_rows))

    if history or review.get("verdict"):
        parts.append("<h2>审核与返工</h2>")
        review_rows = []
        for item in history:
            findings = item.get("findings") or []
            review_rows.append([
                escape(str(item.get("attempt"))),
                f"<code>{escape(str(item.get('commit'))[:10])}</code>",
                f"<span class='tag'>{escape(str(item.get('verdict')))}</span>",
                escape(str(len(findings))),
                escape(redact(str(item.get("summary") or ""), root))[:400],
            ])
        parts.append(table(["轮次", "审核锚点", "结论", "发现数", "说明"], review_rows, nowrap_first=True))
        findings = (review.get("findings") or [])
        if not findings and history:
            # 审核最终通过时 review_report 没有发现，结论来自最后一轮之前的那一轮。
            findings = next((item.get("findings") or [] for item in reversed(history[:-1] or history)
                             if item.get("findings")), [])
        if findings:
            parts.append("<h3>审核发现</h3>")
            parts.append(table(["严重程度", "位置", "观察", "建议"],
                               [[escape(str(item.get("severity"))),
                                 f"<code>{escape(str(item.get('path')))}:{escape(str(item.get('start_line')))}</code>",
                                 escape(redact(str(item.get("observation") or ""), root)),
                                 escape(redact(str(item.get("suggestion") or ""), root))] for item in findings]))

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

    if pull:
        parts.append("<h2>远端产物</h2>")
        parts.append(
            "<table><tbody>"
            f"<tr><th>草稿 PR</th><td><a class='url' href='{escape(str(pull.get('url') or '#'))}'>"
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

    return DOCUMENT.format(title=escape(title), body="\n".join(parts))


def read_events(run_dir: Path) -> list[dict]:
    path = run_dir / "events.jsonl"
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return events


def build(state: dict, run_dir: Path, root: Path) -> str:
    return render(state, run_dir, read_events(run_dir), root)


def write(state: dict, run_dir: Path, root: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(build(state, run_dir, root), encoding="utf-8")
    return destination
