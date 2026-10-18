"""任务检查点与跨进程互斥：进程退出自动释放锁，SQLite 保留最后一次安全状态。"""

from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
from threading import RLock


class StateError(RuntimeError):
    """状态、审批版本或恢复条件不满足。"""


def timestamp():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def job_lock(root: Path, run_id: str):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}", run_id):
        raise StateError("invalid_run_id")
    directory = root / "locks"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / f"{run_id}.lock").open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise StateError("job_is_running") from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class JobStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "jobs.sqlite3"
        self.lock = RLock()
        with closing(self.connect()) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, state TEXT NOT NULL, "
                       "issue_key TEXT UNIQUE, revision INTEGER NOT NULL, updated_at TEXT NOT NULL)")

    def connect(self):
        return sqlite3.connect(self.path, timeout=15)

    def create(self, state: dict, issue_key: str | None = None, retry_of: str | None = None) -> dict:
        with self.lock, closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if retry_of and issue_key:
                changed = db.execute("UPDATE jobs SET issue_key = NULL WHERE id = ? AND issue_key = ?",
                                     (retry_of, issue_key))
                if changed.rowcount != 1:
                    raise StateError("issue_already_has_new_attempt")
            if issue_key:
                row = db.execute("SELECT state, revision FROM jobs WHERE issue_key = ?", (issue_key,)).fetchone()
                if row:
                    return json.loads(row[0]) | {"_revision": row[1]}
            db.execute("INSERT INTO jobs VALUES (?, ?, ?, 0, ?)",
                       (state["run_id"], json.dumps(state, ensure_ascii=False), issue_key, timestamp()))
        state["_revision"] = 0
        return state

    def get(self, run_id: str) -> dict:
        with closing(self.connect()) as db:
            row = db.execute("SELECT state, revision FROM jobs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise StateError("job_not_found")
        return json.loads(row[0]) | {"_revision": row[1]}

    def save(self, state: dict):
        with self.lock, closing(self.connect()) as db, db:
            revision = state["_revision"]
            data = {k: v for k, v in state.items() if k != "_revision"}
            changed = db.execute("UPDATE jobs SET state = ?, revision = revision + 1, updated_at = ? "
                                 "WHERE id = ? AND revision = ?",
                                 (json.dumps(data, ensure_ascii=False), timestamp(), state["run_id"], revision))
            if changed.rowcount != 1:
                raise StateError("checkpoint_conflict")
            state["_revision"] = revision + 1

    def list(self):
        with closing(self.connect()) as db:
            rows = db.execute("SELECT state FROM jobs ORDER BY updated_at DESC LIMIT 100").fetchall()
        return [{k: state.get(k) for k in ("run_id", "status", "phase", "issue")}
                for state in (json.loads(row[0]) for row in rows)]
