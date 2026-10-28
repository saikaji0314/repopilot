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
                   "review_perspectives_finished", "review_merged", "review_reused",
                   "rework_started", "rework_finished", "rework_limit_reached", "review_skipped",
                   "dispute_ab_finished", "dispute_arbitration_finished",
                   "run_error", "run_started", "run_finished", "model_failed", "phase_changed",
                   "plan_recorded", "plan_invalid", "subtask_started", "subtask_finished",
                   "subtask_blocked"}
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
            suffix = f"（{escape(str(event['findings']))} 条待处理发现）" if 'findings' in event else ""
            disputes = f"，争议 {escape(str(event['disputes']))}" if event.get('disputes') else ""
            text = f"审核第 {reviews} 轮结论：{escape(str(event.get('verdict')))}{suffix}{disputes}"
        elif name == "review_perspectives_finished":
            verdicts = event.get("verdicts") or {}
            text = "双视角审核完成：" + escape("；".join(
                f"{key}={value}" for key, value in verdicts.items()))
        elif name == "review_merged":
            text = (f"合并审核发现 {escape(str(event.get('findings')))} 条"
                    f"（阻断 {escape(str(event.get('blocking')))} 条，来源 {escape(str(event.get('sources')))} 份）")
        elif name == "review_reused":
            text = (f"复用同一提交与依据的审核结论（第 {escape(str(event.get('round')))} 轮，"
                    f"{escape(str(event.get('verdict')))}）")
        elif name == "dispute_ab_finished":
            text = (f"争议 {escape(str(event.get('dispute')))} A／B 独立复核："
                    f"A={escape(str(event.get('a')))}，B={escape(str(event.get('b')))}")
        elif name == "dispute_arbitration_finished":
            text = (f"争议 {escape(str(event.get('dispute')))} C 核对结论："
                    f"{escape(str(event.get('outcome')))}")
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
        elif name == "plan_recorded":
            text = (f"计划 {escape(str(event.get('version')))}：{escape(str(event.get('split')))}，"
                    f"{escape(str(event.get('subtasks')))} 个子任务")
        elif name == "subtask_started":
            text = f"开始子任务 {escape(str(event.get('subtask')))}：{escape(str(event.get('title') or ''))}"
        elif name == "subtask_finished":
            text = (f"子任务 {escape(str(event.get('subtask')))} 完成，提交 "
                    f"<code>{escape(str(event.get('commit'))[:10])}</code>")
        elif name == "subtask_blocked":
            text = f"子任务 {escape(str(event.get('subtask')))} 停止：{escape(str(event.get('status')))}"
        elif name == "plan_invalid":
            text = f"计划不可执行：{escape(str(event.get('reason')))}"
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
        return ('<p class="note">返工达到上限，需要人工判断是继续、改需求还是放弃。'
                '可补充需求后 resume，或以 <code>repopilot review continue RUN_ID --extra 1 --reason "..."</code> '
                '明确增加一次修改；也可用 retry 开始新尝试。两类决定都不会自动批准或推送。</p>')
    if status == "review_inconclusive":
        return ('<p class="note">审核无法判断（报告不合法或证据不足），需要人工核对独立复核结果与候选改动；'
                '不能用“人工看了一眼”代替新的检查或批准。</p>')
    if status == "needs_input":
        dispute = [r for r in state.get("human_requests", [])
                   if r.get("status") == "pending" and r.get("kind") == "dispute"]
        if dispute:
            request = dispute[-1]
            identifier = str((request.get("blocking") or [{}])[0].get("dispute") or "d-1")
            return (f'<p class="note">审核争议需要负责人判断。用 '
                    f'<code>repopilot review decide {safe_run} --dispute {escape(identifier)} '
                    '--decision confirmed|refuted --reason "..."</code> 明确该发现是否成立，'
                    '再 resume 继续；同一提交同一争议不会重复复核。</p>')
        if any(r.get("status") == "pending" and r.get("kind") == "context_update"
               for r in state.get("human_requests", [])):
            return ('<p class="note">资料依据已变化或不可用。核查并确认资料后，用 '
                    '<code>repopilot knowledge refresh</code>指定运行、请求ID、请求版本、当前提交和理由，'
                    '再显式 resume；刷新会重新实现、检查和审核，不会批准交付。</p>')
        pending = [r for r in state.get("human_requests", [])
                   if r.get("status") == "pending" and r.get("kind") == "clarification"]
        if pending and state.get("issue"):
            request = pending[-1]
            delivery = request.get("delivery") or {}
            if delivery.get("status") not in {"delivered", "reused", "answered"}:
                return (f'<p class="note">等待业务澄清。可用 <code>repopilot human post {safe_run}</code> '
                        '把这一批问题发到父Issue评论，负责人回复后用 <code>repopilot human sync '
                        f'{safe_run}</code>导入；没有平台凭证时仍可用 <code>repopilot human answer</code> '
                        '在本机答复。等待期间不消耗模型调用。</p>')
            if request.get("final"):
                return ('<p class="note">自动集中提问已达到上限，当前只是最终缺口说明。请负责人补充资料或'
                        '明确改变方案后再 resume；原提问计数保留，不会自动开始下一批。</p>')
            return (f'<p class="note">问题已发到父Issue评论，等待登记负责人回复；'
                    f'用 <code>repopilot human sync {safe_run}</code> 只读核对并导入答复，'
                    '其他人的评论不会推进任务。</p>')
        return (f'<p class="note">等待需求补充。用 <code>repopilot human list {safe_run}</code> '
                '查看请求，再用 <code>repopilot human answer</code> 指定请求ID、版本与答复；'
                '记录答复后显式 resume，答复不会批准或推送代码。</p>')
    if status in {"failed", "workspace_error", "phase_error", "limit_reached"}:
        if str(state.get("stop_reason") or "").startswith("invalid_plan_"):
            return ('<p class="note">计划不可执行，未进入实现。用 <code>repopilot plan show RUN_ID</code> '
                    '查看依赖或子任务问题，补充需求后 retry；环形依赖或缺失前置产物不会被强行执行。</p>')
        return '<p class="note">运行未完成，需要查看失败原因后决定 retry 或调整配置。</p>'
    if status == "completed" and str(state.get("stop_reason") or "").startswith("subtask_"):
        return ('<p class="note">计划子任务报告阻塞，未形成可交付候选；这不是完成交付。'
                '核对 <code>repopilot plan show RUN_ID</code> 后可用 retry 保留现场继续，'
                '费用和提问次数不会清零。</p>')
    return ""
