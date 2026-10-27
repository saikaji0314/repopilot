"""Issue 评论渠道：集中问题正文、登记负责人识别与引用过滤；不做网络请求。"""

from datetime import datetime, timezone
import os
from pathlib import Path
import re

from dotenv import dotenv_values

from repopilot.core.paths import default_env_file
from repopilot.knowledge.documents import KnowledgeFault, checked_body
from repopilot.storage.jobs import StateError
from repopilot.planning.contracts import normalize

OWNER_ENV = "REPOPILOT_OWNER"
OWNER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$"
MARKER_PREFIX = "<!-- repopilot-question:"
MAX_COMMENT_CHARS = 60_000


def load_owner(env_file=None):
    """首版只登记一位负责人；未登记时不猜测评论作者身份。"""
    name = os.environ.get(OWNER_ENV)
    if not name or not name.strip():
        env_file = default_env_file() if env_file is None else Path(env_file)
        values = dotenv_values(env_file, interpolate=False) if env_file.exists() else {}
        name = values.get(OWNER_ENV)
    name = str(name or "").strip()
    if not name:
        raise StateError("registered_owner_required")
    if not re.fullmatch(OWNER_PATTERN, name):
        raise StateError("registered_owner_invalid")
    return name


def marker(request):
    return f"{MARKER_PREFIX}{request.id} -->"


def batch_body(state, request):
    """把一批问题写成一条评论；编号、阻塞对象和依据摘要都在正文里。"""
    limit = int(state.get("question_limit") or 3)
    used = len(state.get("question_batches") or [])
    if request.final:
        title = f"## 最终缺口说明（自动集中提问已达 {limit} 批上限）"
    else:
        title = f"## RepoPilot 第 {request.batch} 批集中澄清（{request.batch}/{limit}）"
    lines = [marker(request), title, "",
             f"- 运行：`{state.get('run_id')}`",
             f"- 阻塞阶段：`{request.phase}`",
             f"- 依据摘要：{request.summary}", ""]
    blocking = {item.get("question"): item for item in request.blocking or []}
    lines.append("### 需要负责人确认的问题")
    for number, question in enumerate(request.questions, 1):
        item = blocking.get(question) or {}
        target = f"（阻塞：{item.get('phase')}" + (f" / 子任务 {item.get('subtask')}" if item.get("subtask") else "") + "）"
        lines.append(f"{number}. {question}{target}")
    lines.extend(["", "请登记负责人直接回复本评论；机器人、其他人和引用文本只作线索，不会自动推进。",
                  "不要在评论里填写密钥、原始会话或私密现场材料。"])
    body = "\n".join(lines)
    try:
        checked_body(body)
    except KnowledgeFault as exc:
        raise StateError(f"question_comment_invalid_{exc}") from None
    if len(body.encode("utf-8")) > MAX_COMMENT_CHARS:
        raise StateError("question_comment_too_large")
    return body


def reply_text(body):
    """去掉引用行和 HTML 标记；只剩引用的评论不能当业务答复。"""
    text = re.sub(r"<!--.*?-->", "", str(body or ""), flags=re.S)
    lines = [line for line in text.splitlines() if not line.lstrip().startswith(">")]
    return "\n".join(lines).strip()


def _echo(text, request):
    value = normalize(text)
    candidates = {normalize(item) for item in request.questions}
    candidates.add(normalize(request.summary))
    return not value or value in candidates


def _moment(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        result = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return result if result.tzinfo else result.replace(tzinfo=timezone.utc)


def _ordered(comments):
    """按 created_at 排序；缺失时间时保留平台返回顺序，交给调用方按ID兜底。"""
    timed = [(index, item, _moment(item.get("created_at"))) for index, item in enumerate(comments)]
    if any(moment is None for _, _, moment in timed):
        return list(comments)
    return [item for _, item, _ in sorted(timed, key=lambda entry: (entry[2], entry[0]))]


def select_reply(comments, request, *, owner, batch_id=None, batch_created_at=None):
    """按时间顺序找到批次评论之后的第一条有效负责人答复。

    返回 (答复, 被忽略的有效候选及原因)。优先按发送时保存的评论ID定位批次，
    避免别人复制标记后改变边界；没有保存ID的旧运行才退回标记查找。
    批次评论不存在时返回 (None, [])，不能从别处推断答案。
    """
    ordered = _ordered(comments)
    if batch_id is not None:
        position = next((index for index, item in enumerate(ordered)
                         if item.get("id") == batch_id), None)
    else:
        marker_text = marker(request)
        position = next((index for index, item in enumerate(ordered)
                         if marker_text in str(item.get("body") or "")), None)
    if position is None:
        return None, []
    boundary = _moment(batch_created_at) or _moment(ordered[position].get("created_at"))
    accepted, later = None, []
    for item in ordered[position + 1:]:
        body = str(item.get("body") or "")
        author = str(item.get("author") or "")
        if item.get("system"):
            later.append(comment_source(item, reason="system"))
            continue
        if str(item.get("author_type") or "").casefold() == "bot" or author.casefold().endswith("[bot]"):
            later.append(comment_source(item, reason="bot"))
            continue
        if author.casefold() != str(owner).casefold():
            later.append(comment_source(item, reason="not_registered_owner"))
            continue
        # RepoPilot 自己的批次正文以HTML标记开头；负责人引用它会以">"开头，不能在这里整条丢弃。
        if body.lstrip().startswith("<!-- repopilot-"):
            later.append(comment_source(item, reason="repopilot_comment"))
            continue
        moment = _moment(item.get("created_at"))
        if boundary is not None and moment is not None and moment <= boundary:
            later.append(comment_source(item, reason="before_batch"))
            continue
        text = reply_text(body)
        if _echo(text, request):
            later.append(comment_source(item, reason="quoted_or_echo"))
            continue
        try:
            checked_body(text)
        except KnowledgeFault:
            later.append(comment_source(item, reason="invalid_content"))
            continue
        if len(text) > 8000:
            later.append(comment_source(item, reason="too_long"))
            continue
        if accepted is None:
            accepted = {**comment_source(item), "text": text}
        else:
            later.append(comment_source(item, reason="later_reply_ignored"))
    return accepted, later


def comment_source(comment, *, reason=None):
    """只保留可回溯的最小字段，不把完整评论正文写进运行状态。"""
    source = {"comment_id": comment.get("id"), "url": comment.get("url"),
              "author": comment.get("author"), "created_at": comment.get("created_at")}
    if reason:
        source["ignored_reason"] = reason
    return {key: value for key, value in source.items() if value is not None}
