"""Issue 队列的持久化、领取与人工恢复；不负责轮询或启动进程。"""

from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from repopilot.delivery.forge import ForgeError, parse_issue_url
from repopilot.delivery.health import heartbeat_health
from repopilot.storage.jobs import JobStore, StateError, timestamp


class IssueQueue:
    def __init__(self, root):
        self.store = JobStore(Path(root))
        self.root = self.store.root
        with closing(self.store.connect()) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS watch_targets (id TEXT PRIMARY KEY, activated_at TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS issue_queue (issue_key TEXT PRIMARY KEY, repository TEXT NOT NULL, "
                "url TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL, run_id TEXT, error TEXT, created_at TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS watcher_runtime (id INTEGER PRIMARY KEY, pid INTEGER, status TEXT, heartbeat_at TEXT)")

    def heartbeat(self, status):
        with closing(self.store.connect()) as db, db:
            db.execute("INSERT OR REPLACE INTO watcher_runtime VALUES (1, ?, ?, ?)", (os.getpid(), status, timestamp()))

    def health(self):
        with closing(self.store.connect()) as db:
            row = db.execute("SELECT pid,status,heartbeat_at FROM watcher_runtime WHERE id=1").fetchone()
        record = None if row is None else dict(zip(("pid", "status", "heartbeat_at"), row))
        return heartbeat_health(record)

    def activation(self, repository, label):
        key = repository.lower() + ":" + (label or "*")
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        with closing(self.store.connect()) as db, db:
            db.execute("INSERT OR IGNORE INTO watch_targets VALUES (?, ?)", (key, now))
            return db.execute("SELECT activated_at FROM watch_targets WHERE id = ?", (key,)).fetchone()[0]

    def enqueue(self, issue, repository, template, overrides):
        parsed = parse_issue_url(issue["html_url"])
        if parsed["repository"].lower() != repository.lower() or parsed["number"] != issue["number"]:
            raise ForgeError("issue_identity_mismatch")
        repo, number = parsed["repository"], parsed["number"]
        key = f"{repo.lower()}#{number}"
        with closing(self.store.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM jobs WHERE lower(issue_key) = ?", (key,)).fetchone():
                return False
            changed = db.execute("INSERT OR IGNORE INTO issue_queue VALUES (?, ?, ?, ?, 'queued', NULL, NULL, ?)",
                (key, repository.lower(), issue["html_url"], json.dumps({"task": template.model_dump(mode="json"),
                    "settings": overrides}, ensure_ascii=False), timestamp()))
            return changed.rowcount == 1

    def claim(self, max_per_repository):
        with closing(self.store.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            # 未知的旧 worker 可能仍在运行；核查前暂停新分派，不能借重启放大并发。
            if db.execute("SELECT 1 FROM issue_queue WHERE status='interrupted' LIMIT 1").fetchone():
                return None
            row = db.execute("SELECT issue_key, repository, url, payload FROM issue_queue q WHERE status = 'queued' "
                "AND (SELECT count(*) FROM issue_queue r WHERE r.repository=q.repository AND r.status='running') < ? "
                "ORDER BY created_at, issue_key LIMIT 1", (max_per_repository,)).fetchone()
            if row is None:
                return None
            db.execute("UPDATE issue_queue SET status='running' WHERE issue_key=?", (row[0],))
            return {"issue_key": row[0], "repository": row[1], "url": row[2], **json.loads(row[3])}

    def bind(self, key, run_id):
        with closing(self.store.connect()) as db, db:
            db.execute("UPDATE issue_queue SET run_id=? WHERE issue_key=?", (run_id, key))

    def finish(self, key, status, error=None):
        with closing(self.store.connect()) as db, db:
            db.execute("UPDATE issue_queue SET status=?, error=? WHERE issue_key=?", (status, error, key))

    def mark_interrupted(self):
        # 上次调度器退出时子进程可能仍有副作用，禁止把 running 自动放回 queued。
        with closing(self.store.connect()) as db, db:
            return db.execute("UPDATE issue_queue SET status='interrupted', error='inspect_before_manual_resume' "
                              "WHERE status='running'").rowcount

    def acknowledge(self, key, reason):
        """操作者核查进程及副作用后的显式确认；不回放或删除原任务。"""
        if not reason.strip():
            raise StateError("reason_required")
        with closing(self.store.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status,run_id FROM issue_queue WHERE issue_key=?", (key,)).fetchone()
            if not row or row[0] != "interrupted":
                raise StateError("queue_item_not_interrupted")
            if row[1] and self.store.get(row[1])["status"] in {"running", "publishing"}:
                raise StateError("worker_still_running_or_requires_recovery")
            db.execute("UPDATE issue_queue SET status='acknowledged', error=? WHERE issue_key=?",
                       ("operator_acknowledged: " + reason, key))

    def list(self):
        with closing(self.store.connect()) as db:
            result = [dict(zip(("issue_key", "status", "run_id", "error"), row)) for row in db.execute(
                "SELECT issue_key,status,run_id,error FROM issue_queue ORDER BY created_at DESC LIMIT 100")]
        for item in result:
            if item["run_id"]:
                item["run_status"] = self.store.get(item["run_id"])["status"]
        return result
