"""报告中的有界读取、转义、脱敏与证据格式，不执行仓库代码。"""

from html import escape
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

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


def safe_url(value) -> str:
    """证据中的链接是数据，禁止 javascript/data 等可执行 URL。"""
    try:
        parsed = urlsplit(str(value or ''))
        if parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password:
            return str(value)
    except ValueError:
        pass
    return '#'


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
    return picked if limit is None else picked[-limit:]


def timeline_rows(events: list[dict], root: Path, limit: int = 24) -> list[list[str]]:
    """按时间顺序编号审核轮次与返工次数。

    审核事件里的 attempt 是审核轮次，返工事件里是返工次数，语义不同，
    因此各自独立计数，不直接复用字段值。
    """

    rows, reviews, reworks = [], 0, 0
    for event in timeline_events(events, None):
        name = str(event.get("event"))
        moment = str(event.get("time") or "")[:19].replace("T", " ")
        if name == "review_started":
            reviews += 1
            text = f"开始审核第 {reviews} 轮，锚点提交 <code>{escape(str(event.get('commit'))[:10])}</code>"
        elif name == "review_finished":
            suffix = f"（{escape(str(event['findings']))} 条发现）" if 'findings' in event else ""
            text = f"审核第 {reviews} 轮结论：{escape(str(event.get('verdict')))}{suffix}"
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
    return rows[-limit:]


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
        handle = f"<code>repopilot approve {safe_run} --commit {safe_commit} --reason \"已核对差异与检查\"</code>"
        return f'<p class="note">等待人工审批：核对上方候选改动与独立复核后用 {handle} 批准，批准本身不推送。</p>'
    if status == "approved":
        return (f'<p class="note">已批准提交 <code>{safe_commit}</code>，'
                f'下一步用 <code>repopilot publish {safe_run}</code> 创建草稿 PR。</p>')
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
