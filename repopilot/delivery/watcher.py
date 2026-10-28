"""持久化 Issue 队列与有上限的进程调度；失败任务不会自动无限重试。"""

from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
import json
import multiprocessing
from pathlib import Path
import time

from pydantic import Field, field_validator
import yaml

from repopilot.core.config import Contract, RuntimeSettings, Task, execution_options, load_settings, load_task
from repopilot.delivery.forge import ForgeError, open_forge, remote_origin, repository_source_key
from repopilot.delivery.queue import IssueQueue  # 兼容原 watcher.IssueQueue 入口
from repopilot.orchestration import human
from repopilot.storage.jobs import JobStore, StateError, job_lock, timestamp
from repopilot.orchestration import workflow as workflow


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
    poll_comments: bool = True
    targets: list[WatchTarget] = Field(min_length=1, max_length=10)


def load_watch_config(path):
    path = Path(path).resolve(strict=True)
    config = WatchConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8-sig")))
    for target in config.targets:
        target.task = (path.parent / target.task).resolve(strict=True)
        load_task(target.task)
    return config


def poll_once(config, queue, forge=None):
    """按模板仓库的远端来源选择平台；同一个监听器可以同时盯 GitHub 与 GitLab。

    `forge` 参数保留给测试注入；正常轮询时按每个目标仓库自己的 origin 打开客户端。
    """
    count = 0
    for target in config.targets:
        template = load_task(target.task)
        origin = remote_origin(template.repository)
        repository = origin.repository
        activated = queue.activation(repository_source_key(origin), target.label)
        client = forge or open_forge(origin)
        try:
            issues = client.open_issues(repository, label=target.label,
                                        since=None if target.include_existing else activated)
            for issue in issues:
                if "pull_request" in issue or issue.get("state") != "open":
                    continue
                created = datetime.fromisoformat(issue["created_at"].replace("Z", "+00:00"))
                if not target.include_existing and created < datetime.fromisoformat(activated):
                    continue
                if target.label and target.label not in [v["name"] for v in issue.get("labels", [])]:
                    continue
                count += queue.enqueue(issue, repository, template, target.settings)
        finally:
            if forge is None:
                client.close()
    return count


def run_queued_job(root, item):
    """子进程执行一个任务，审批等待只是持久化状态，不占 worker。"""
    queue = IssueQueue(root)
    forge = None
    try:
        settings = load_settings()
        settings = RuntimeSettings.model_validate({**settings.model_dump(), **item["settings"]})
        forge = open_forge(item["url"])
        task, issue = forge.intake(item["url"], Task.model_validate(item["task"]))
        base_commit = forge.prepare_base(task.repository, issue["repository"])
        state = workflow.create_job(task, settings, Path(root), issue=issue, base_commit=base_commit)
        queue.bind(item["issue_key"], state["run_id"])
        forge.close()
        forge = None
        result = workflow.resume(state["run_id"], settings, Path(root))
        queue.finish(item["issue_key"], result["status"])
        return {"issue_key": item["issue_key"], "run_id": state["run_id"], "status": result["status"]}
    except Exception as exc:
        error = str(exc) if isinstance(exc, (StateError, ForgeError)) else type(exc).__name__
        queue.finish(item["issue_key"], "failed", error)
        return {"issue_key": item["issue_key"], "status": "failed", "error": error}
    finally:
        if forge:
            forge.close()


def run_waiting_job(root, run_id):
    """有节制地核对一个等待中的Issue批次：必要时自动投递，再读取负责人回复并恢复。"""
    try:
        settings = load_settings()
        result = workflow.resume(run_id, settings, Path(root))
        synced = human.sync_issue(run_id, Path(root))
        if synced.get("synced"):
            result = workflow.resume(run_id, settings, Path(root))
        state = JobStore(root).get(run_id)
        issue = state.get("issue") or {}
        if issue.get("repository") and issue.get("number"):
            IssueQueue(root).finish_run(run_id, result.get("status", state["status"]))
        return {"kind": "comment", "run_id": run_id, "status": result.get("status"),
                "sync": {key: synced.get(key) for key in ("synced", "already_answered", "reason")},
                "auto_comment": result.get("auto_comment")}
    except Exception as exc:
        return {"kind": "comment", "run_id": run_id, "status": "failed",
                "error": str(exc) if isinstance(exc, (StateError, ForgeError)) else type(exc).__name__}


def emit(event, **values):
    print(json.dumps({"time": timestamp(), "event": event, **values}, ensure_ascii=True), flush=True)


def watch(config, root, *, once=False):
    """一个监听进程配有界进程池；轮询、评论同步和工作进程都在前台生命周期内受控。"""
    queue = IssueQueue(root)
    with job_lock(queue.root, "issue-watcher"):
        stop_file = queue.root / "watcher.stop"
        stop_file.unlink(missing_ok=True)
        interrupted = queue.mark_interrupted()
        if interrupted:
            emit("attention_required", interrupted=interrupted)
        futures, comment_futures = {}, {}
        next_poll = next_comment_poll = 0
        failures, polled, comment_polled, failed_poll = 0, False, False, False
        next_heartbeat = 0
        try:
            with ProcessPoolExecutor(max_workers=config.max_workers,
                                     mp_context=multiprocessing.get_context("spawn")) as pool:
                emit("watch_started", max_workers=config.max_workers, max_per_repository=config.max_per_repository,
                     poll_comments=config.poll_comments)
                try:
                    while True:
                        now = time.monotonic()
                        if now >= next_heartbeat:
                            queue.heartbeat("running")
                            next_heartbeat = now + 5
                        if stop_file.exists():
                            emit("watch_stopping", active_workers=len(futures) + len(comment_futures))
                            break
                        if (not once or not polled) and now >= next_poll:
                            try:
                                added = poll_once(config, queue)
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
                                emit("poll_failed", error=str(exc) if isinstance(exc, ForgeError) else type(exc).__name__, retry_after=delay)
                            polled = True
                        if stop_file.exists():
                            emit("watch_stopping", active_workers=len(futures) + len(comment_futures))
                            break
                        for future in list(futures):
                            if future.done():
                                item = futures.pop(future)
                                try:
                                    emit("job_finished", **future.result())
                                except Exception as exc:
                                    queue.finish(item["issue_key"], "interrupted", type(exc).__name__)
                                    emit("worker_interrupted", issue_key=item["issue_key"], error=type(exc).__name__)
                        for future in list(comment_futures):
                            if future.done():
                                info = comment_futures.pop(future)
                                run_id = info["run_id"]
                                try:
                                    result = future.result()
                                except Exception as exc:
                                    result = {"status": "failed", "error": type(exc).__name__}
                                event = ("comment_job_finished" if result.get("status") != "failed"
                                         else "comment_job_failed")
                                emit(event, **({"run_id": run_id} | result))
                                if result.get("status") == "failed" and info.get("key"):
                                    try:
                                        state = JobStore(queue.root).get(run_id)
                                    except StateError:
                                        state = {}
                                    if state.get("status") == "needs_input":
                                        queue.restore_comment(info["key"], "needs_input",
                                                              str(result.get("error") or "comment_resume_failed"))
                                    else:
                                        queue.finish(info["key"], state.get("status") or "failed",
                                                     str(result.get("error") or "comment_resume_failed"))
                        while len(futures) + len(comment_futures) < config.max_workers:
                            item = queue.claim(config.max_per_repository)
                            if item is None:
                                break
                            futures[pool.submit(run_queued_job, str(queue.root), item)] = item
                            emit("job_dispatched", issue_key=item["issue_key"])
                        if config.poll_comments and now >= next_comment_poll:
                            slots = config.max_workers - len(futures) - len(comment_futures)
                            if slots > 0:
                                busy = {item["run_id"] for item in comment_futures.values()}
                                try:
                                    waiting = human.waiting_comment_runs(queue.root)
                                except Exception as exc:
                                    waiting = []
                                    emit("comment_poll_failed", error=type(exc).__name__)
                                for run_id in waiting:
                                    if slots <= 0:
                                        break
                                    if run_id in busy:
                                        continue
                                    state = JobStore(queue.root).get(run_id)
                                    issue = state.get("issue") or {}
                                    key = (f"{issue['repository'].lower()}#{issue['number']}"
                                           if issue.get("repository") and issue.get("number") else None)
                                    if key:
                                        try:
                                            allowed = queue.begin_comment(key, config.max_per_repository)
                                        except Exception as exc:
                                            emit("comment_poll_failed", error=type(exc).__name__)
                                            continue
                                        if not allowed:
                                            continue
                                    comment_futures[pool.submit(
                                        run_waiting_job, str(queue.root), run_id)] = {
                                            "run_id": run_id, "key": key}
                                    busy.add(run_id)
                                    slots -= 1
                                    emit("comment_job_dispatched", run_id=run_id)
                                next_comment_poll = time.monotonic() + config.poll_seconds
                                comment_polled = True
                        if (once and polled and (not config.poll_comments or comment_polled)
                                and not futures and not comment_futures):
                            return {"status": "error" if failed_poll else "completed", "queue": queue.list()}
                        time.sleep(0.5 if once else 1)
                except KeyboardInterrupt:
                    # 停止接新任务，等待已开始 worker 落盘，避免随意杀死检查进程树。
                    emit("watch_stopping", active_workers=len(futures) + len(comment_futures))
        finally:
            queue.heartbeat("stopped")
        return {"status": "paused", "queue": queue.list()}
