"""只读加载运行证据并组装离线报告；不调用模型、GitHub 或审批。"""

from html import escape
import json
from pathlib import Path

from . import sections
from .layout import shell, overview
# 保留旧 report 模块的格式化函数导出，兼容既有调用方。
from .formatting import (redact, safe_url, load_json, tail, status_class, table,
                         timeline_events, timeline_rows, load_meta, next_action)


def render(state: dict, run_dir: Path, events: list[dict], root: Path) -> str:
    issue = state.get("issue") or {}
    meta = load_meta(run_dir)
    changes = load_json(run_dir / "changes.json") or {}
    headline = str(issue.get("title") or meta.get("title") or state.get("run_id") or run_dir.name)
    parts = []
    if meta.get("description"):
        parts.append(f"<p>{escape(redact(str(meta['description']), root))}</p>")
    parts.extend([
        sections.requirement(state, root),
        sections.metrics(state),
        '<h2>阶段时间线</h2>',
        table(["时间（UTC）", "事件", "说明"], timeline_rows(events, root)),
        sections.implementation(state.get("report") or {}, root),
        sections.subtask_reports(state, root),
        sections.integrations(state, root),
        sections.parallel_blocks(state, root),
        sections.plan(state, root),
        sections.human_requests(state, root),
        sections.materials(state, root),
        sections.candidate(changes, root),
        sections.checks(state.get("verified") or [], root),
        sections.reviews(state.get("history") or [], state.get("review_report") or {}, root),
        sections.decision(state.get("approval") or {}, root),
        sections.remote(state.get("pull_request") or {}, state.get("publication") or {},
                        state.get("approval") or {}, root),
    ])
    title = f"RepoPilot 运行报告 · {headline}"
    return redact(shell(title, "\n".join(parts), overview(state, changes, headline, next_action(state)), state), root)

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
