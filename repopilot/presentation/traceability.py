"""只读呈现版本、人工来源与决定正文，不访问平台或改变知识索引。"""

from html import escape
import json
import sqlite3

from .formatting import redact, safe_url, table


def text(value, root):
    return escape(redact(str(value if value is not None else "未记录"), root))


def source(value, root):
    """来源既可能是资料ID，也可能是外部链接；只链接经过校验的HTTPS地址。"""
    label = text(value, root)
    url = safe_url(value)
    return label if url == "#" else f"<a class='url' href='{escape(url)}'>{label}</a>"


def render(state, root):
    parts = []
    issue = state.get("issue") or {}
    if issue.get("url"):
        from repopilot.delivery.forge import ForgeError, parse_issue_url
        try:
            identity = parse_issue_url(issue["url"])
        except ForgeError:
            parts.append('<h2>平台来源身份</h2><p class="note">旧来源链接无法识别，请回查原工单。</p>')
        else:
            parts.append('<h2>平台来源身份</h2>' + table(["平台", "实例", "项目", "工单号"], [[
                text(identity[key], root) for key in ("provider", "host", "repository", "number")]]))
    basis = state.get("review_basis") or (state.get("review_report") or {}).get("basis") or {}
    if basis:
        values = [("审核依据摘要", basis.get("hash")), ("固定候选", basis.get("commit")),
                  ("固定基线", basis.get("base_commit")), ("独立检查摘要", basis.get("verified_sha256")),
                  ("资料清单", basis.get("knowledge_snapshot_id")),
                  ("任务说明版本", basis.get("brief_revision")), ("计划版本", basis.get("plan_version")),
                  ("需求答复版本", basis.get("requirements_revision"))]
        parts.append('<h2>版本与来源</h2>' + table(["依据", "固定值"],
                     [[label, f"<code>{text(value, root)}</code>"] for label, value in values]))
    requests = state.get("human_requests") or []
    if requests:
        pending = [item for item in requests if item.get("status") == "pending"]
        if pending:
            owner = (state.get("platform_preflight") or {}).get("registered_owner") or "本地操作者／登记负责人"
            rows = [[text(item.get("id"), root), text(item.get("kind"), root),
                     text(item.get("summary"), root), text(owner, root),
                     text("；".join(str(block.get("subtask") or block.get("phase") or "父任务")
                                    for block in item.get("blocking") or []) or item.get("phase"), root)]
                    for item in pending]
            parts.append('<h2>当前等待事项</h2>' + table(["请求", "决定类型", "需要决定什么", "等待谁", "受影响任务／阶段"], rows))
        parts.append('<h3>人工请求来源与版本</h3><p class="note">答复业务问题、核对现场和批准交付是不同决定。'
                     '本地操作者身份沿用CLI信任边界。</p>')
        for request in requests:
            reply = request.get("reply") or {}
            context = request.get("context") or {}
            origin = reply.get("source") or {}
            delivery = request.get("delivery") or {}
            values = [("请求版本", text(request.get("version"), root)),
                      ("候选", text(context.get("head_commit"), root)),
                      ("资料清单", text(context.get("knowledge_snapshot_id"), root)),
                      ("动作", text(reply.get("action"), root)), ("身份", text(reply.get("actor"), root)),
                      ("渠道", text(reply.get("channel"), root)), ("平台作者", text(origin.get("author"), root)),
                      ("答复来源", source(origin.get("url"), root) if origin.get("url") else text(origin.get("comment_id"), root)),
                      ("提问投递", source(delivery.get("url"), root) if delivery.get("url") else text(delivery.get("status"), root)),
                      ("答复时间", text(reply.get("at"), root))]
            parts.append(f'<details><summary>{text(request.get("id"), root)}</summary>'
                         + table(["来源字段", "记录值"], values) + '</details>')
    entries = (state.get("knowledge_snapshot") or {}).get("entries") or []
    if entries:
        rows = []
        for item in entries:
            relations = item.get("relations") or {}
            relation_text = "；".join(f"{key}: {', '.join(values)}" for key, values in relations.items())
            rows.append([text(item.get("id"), root), text(item.get("version"), root),
                         text(item.get("status"), root),
                         "<br>".join(source(value, root) for value in item.get("sources") or []) or "未记录",
                         text(relation_text or None, root)])
        parts.append('<h3>资料来源与关系</h3>' + table(["资料", "版本", "状态", "原始来源", "关联"], rows))
    return "\n".join(parts)


def decisions(state, root, *, runtime_root=None):
    """按记录的精确版本取简短正文；缺失或损坏仅标注，不回退到当前版本。"""
    records = state.get("decision_records") or []
    if not records:
        return ""
    from repopilot.knowledge.documents import KnowledgeFault
    from repopilot.knowledge.reader import KnowledgeReader
    from repopilot.execution.workspace import WorkspaceError

    repository = (state.get("task") or {}).get("repository")
    runtime_root = runtime_root if runtime_root is not None else root
    available = repository and (runtime_root / "knowledge.sqlite3").is_file()
    store = KnowledgeReader(runtime_root) if available else None
    parts = ['<h2>决定理由与回查</h2><p class="note">机器决定保持记录时的状态；提议不等于人工确认。'
             '此页只读固定版本，不同步索引或读取原始模型会话。</p>']
    for item in records:
        identifier, version = item.get("id"), item.get("version")
        parts.append(f"<h3>{text(identifier, root)}</h3>" + table(["阶段", "状态", "版本", "资料清单", "代码快照"], [[
            text(item.get(key), root) for key in ("phase", "status", "version", "knowledge_snapshot_id", "code_snapshot")]]))
        try:
            if store is None or not identifier or not version:
                raise KnowledgeFault("knowledge_store_unavailable")
            body = store.read(repository, identifier, version=version, end_line=120, include_events=False)
        except (KnowledgeFault, WorkspaceError, OSError, sqlite3.DatabaseError,
                UnicodeError, json.JSONDecodeError):
            parts.append('<p class="bad">固定版本正文不可用；请回查原运行资料，未用当前版本替代。</p>')
            continue
        parts.append(f"<pre>{text(body['text'], root)}</pre>")
        parts.append('<p class="meta">来源：' + "；".join(source(value, root) for value in body.get("sources") or []) + '</p>')
        if body["total_lines"] > body["end_line"]:
            parts.append(f'<p class="note">正文仅展示前120行，共{text(body["total_lines"], root)}行。'
                         '用 knowledge read 指定上述资料ID与版本继续分页回查。</p>')
    return "\n".join(parts)
