"""持久化 Issue 队列与有上限的进程调度；失败任务不会自动无限重试。"""

from concurrent.futures import ProcessPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
import json
import multiprocessing
import os
from pathlib import Path
import time

from pydantic import Field, field_validator
import yaml

from .config import Contract, RuntimeSettings, Task, execution_options, load_settings, load_task
from .github import GitHub, GitHubError, load_token, parse_issue, remote_repository
from .store import JobStore, StateError, job_lock, timestamp
from . import workflow


class WatchTarget(Contract):
    task: Path
    label: str | None = Field(default="repopilot", min_length=1, max_length=100)
    include_existing: bool = False
    settings: dict = Field(default_factory=dict)

    @field_validator("settings")
    @classmethod
    def trusted_settings(cls, value):
        return execution_options(value)


class WatchConfig(Contract):
    poll_seconds: float = Field(default=60, ge=15, le=3600)
    max_workers: int = Field(default=1, ge=1, le=4)
    max_per_repository: int = Field(default=1, ge=1, le=4)
    targets: list[WatchTarget] = Field(min_length=1, max_length=10)


def load_watch_config(path):
    path = Path(path).resolve(strict=True)
    config = WatchConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8-sig")))
    for target in config.targets:
        target.task = (path.parent / target.task).resolve(strict=True)
        load_task(target.task)
    return config


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
        return None if row is None else dict(zip(("pid", "status", "heartbeat_at"), row))

    def activation(self, repository, label):
        key = repository.lower() + ":" + (label or "*")
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        with closing(self.store.connect()) as db, db:
            db.execute("INSERT OR IGNORE INTO watch_targets VALUES (?, ?)", (key, now))
            return db.execute("SELECT activated_at FROM watch_targets WHERE id = ?", (key,)).fetchone()[0]

    def enqueue(self, issue, repository, template, overrides):
        repo, number = parse_issue(issue["html_url"])
        if repo.lower() != repository.lower() or number != issue["number"]:
            raise GitHubError("issue_identity_mismatch")
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


def poll_once(config, queue, github):
    count = 0
    for target in config.targets:
        template = load_task(target.task)
        repository = remote_repository(template.repository)
        activated = queue.activation(repository, target.label)
        for issue in github.open_issues(repository, label=target.label, since=None if target.include_existing else activated):
            if "pull_request" in issue or issue.get("state") != "open":
                continue
            created = datetime.fromisoformat(issue["created_at"].replace("Z", "+00:00"))
            if not target.include_existing and created < datetime.fromisoformat(activated):
                continue
            if target.label and target.label not in [v["name"] for v in issue.get("labels", [])]:
                continue
            count += queue.enqueue(issue, repository, template, target.settings)
    return count


def run_queued_job(root, item):
    """子进程执行一个任务，审批等待只是持久化状态，不占 worker。"""
    queue = IssueQueue(root)
    github = None
    try:
        settings = load_settings()
        settings = RuntimeSettings.model_validate({**settings.model_dump(), **item["settings"]})
        github = GitHub(load_token())
        task, issue = github.intake(item["url"], Task.model_validate(item["task"]))
        base_commit = github.prepare_base(task.repository, issue["repository"])
        state = workflow.create_job(task, settings, Path(root), issue=issue, base_commit=base_commit)
        queue.bind(item["issue_key"], state["run_id"])
        github.close()
        github = None
        result = workflow.resume(state["run_id"], settings, Path(root))
        queue.finish(item["issue_key"], result["status"])
        return {"issue_key": item["issue_key"], "run_id": state["run_id"], "status": result["status"]}
    except Exception as exc:
        error = str(exc) if isinstance(exc, (StateError, GitHubError)) else type(exc).__name__
        queue.finish(item["issue_key"], "failed", error)
        return {"issue_key": item["issue_key"], "status": "failed", "error": error}
    finally:
        if github:
            github.close()


def emit(event, **values):
    print(json.dumps({"time": timestamp(), "event": event, **values}, ensure_ascii=True), flush=True)


def watch(config, root, *, once=False):
    """一个监听进程配有界进程池；轮询和工作进程都在前台生命周期内受控。"""
    queue = IssueQueue(root)
    with job_lock(queue.root, "issue-watcher"):
        stop_file = queue.root / "watcher.stop"
        stop_file.unlink(missing_ok=True)
        interrupted = queue.mark_interrupted()
        if interrupted:
            emit("attention_required", interrupted=interrupted)
        github = GitHub(load_token())
        futures, next_poll, failures, polled, failed_poll = {}, 0, 0, False, False
        next_heartbeat = 0
        try:
            with ProcessPoolExecutor(max_workers=config.max_workers,
                                     mp_context=multiprocessing.get_context("spawn")) as pool:
                emit("watch_started", max_workers=config.max_workers, max_per_repository=config.max_per_repository)
                try:
                    while True:
                        now = time.monotonic()
                        if now >= next_heartbeat:
                            queue.heartbeat("running")
                            next_heartbeat = now + 5
                        if stop_file.exists():
                            emit("watch_stopping", active_workers=len(futures))
                            break
                        if (not once or not polled) and now >= next_poll:
                            try:
                                added = poll_once(config, queue, github)
                                if added:
                                    emit("issues_queued", count=added)
                                failures, failed_poll = 0, False
                                next_poll = time.monotonic() + config.poll_seconds
                            except Exception as exc:
                                failures += 1
                                failed_poll = True
                                delay = max(config.poll_seconds, min(900, config.poll_seconds * 2 ** min(failures, 5)),
                                            getattr(exc, "retry_after", 0))
                                next_poll = time.monotonic() + delay
                                emit("poll_failed", error=str(exc) if isinstance(exc, GitHubError) else type(exc).__name__, retry_after=delay)
                            polled = True
                        if stop_file.exists():
                            emit("watch_stopping", active_workers=len(futures))
                            break
                        for future in list(futures):
                            if future.done():
                                item = futures.pop(future)
                                try:
                                    emit("job_finished", **future.result())
                                except Exception as exc:
                                    queue.finish(item["issue_key"], "interrupted", type(exc).__name__)
                                    emit("worker_interrupted", issue_key=item["issue_key"], error=type(exc).__name__)
                        while len(futures) < config.max_workers:
                            item = queue.claim(config.max_per_repository)
                            if item is None:
                                break
                            futures[pool.submit(run_queued_job, str(queue.root), item)] = item
                            emit("job_dispatched", issue_key=item["issue_key"])
                        if once and polled and not futures:
                            return {"status": "error" if failed_poll else "completed", "queue": queue.list()}
                        time.sleep(0.5 if once else 1)
                except KeyboardInterrupt:
                    # 停止接新任务，等待已开始 worker 落盘，避免随意杀死检查进程树。
                    emit("watch_stopping", active_workers=len(futures))
        finally:
            github.close()
            queue.heartbeat("stopped")
        return {"status": "paused", "queue": queue.list()}
