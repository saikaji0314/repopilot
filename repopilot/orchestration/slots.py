"""跨运行共享的实现槽位租约；默认最多两个并发实现Agent。"""

from contextlib import closing
import sqlite3
import time
from pathlib import Path

from repopilot.storage.jobs import StateError


class SlotFault(StateError):
    """实现槽位在等待期限内没有空位。"""


class SlotManager:
    """把槽位放在jobs.sqlite3，所有运行共享；进程退出后租约按TTL过期。"""

    def __init__(self, root, *, limit=2, lease_seconds=120):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "jobs.sqlite3"
        self.limit = max(1, int(limit))
        self.lease_seconds = max(30, int(lease_seconds))
        with closing(self.connect()) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS implementation_slots "
                       "(owner TEXT PRIMARY KEY, acquired_at REAL NOT NULL)")

    def connect(self):
        return sqlite3.connect(self.path, timeout=15)

    def _cleanup(self, db, now):
        db.execute("DELETE FROM implementation_slots WHERE acquired_at < ?",
                   (now - self.lease_seconds,))

    def acquire(self, owner, *, timeout=30):
        deadline = time.monotonic() + max(0, timeout)
        while True:
            now = time.time()
            with closing(self.connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                self._cleanup(db, now)
                used = db.execute("SELECT count(*) FROM implementation_slots").fetchone()[0]
                if used < self.limit:
                    db.execute("INSERT OR REPLACE INTO implementation_slots VALUES (?, ?)",
                               (owner, now))
                    return owner
            if time.monotonic() >= deadline:
                raise SlotFault("implementation_slots_busy")
            time.sleep(0.2)

    def touch(self, owner):
        with closing(self.connect()) as db, db:
            db.execute("UPDATE implementation_slots SET acquired_at=? WHERE owner=?",
                       (time.time(), owner))

    def release(self, owner):
        with closing(self.connect()) as db, db:
            db.execute("DELETE FROM implementation_slots WHERE owner=?", (owner,))

    def snapshot(self):
        now = time.time()
        with closing(self.connect()) as db:
            self._cleanup(db, now)
            rows = db.execute("SELECT owner, acquired_at FROM implementation_slots").fetchall()
        return {"limit": self.limit, "used": len(rows),
                "owners": [owner for owner, _ in rows]}
