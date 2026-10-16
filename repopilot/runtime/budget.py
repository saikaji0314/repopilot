"""Persistent, conservative reservations; uncertain calls are never refunded."""

from contextlib import closing
import math
from pathlib import Path
import sqlite3
from uuid import uuid4


class RunLimit(RuntimeError):
    """A local execution or estimate limit was reached before the next action."""


def units(cny: float) -> int:
    if not math.isfinite(cny) or cny < 0:
        raise ValueError("invalid cost")
    return math.ceil(cny * 1_000_000)


class Budget:
    """One explicit budget window per database; no automatic date reset."""

    def __init__(self, path: Path, limit_cny: float):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with closing(self.connect()) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS budget (id INTEGER PRIMARY KEY, cap INTEGER)")
            db.execute("INSERT OR IGNORE INTO budget VALUES (1, ?)", (units(limit_cny),))
            # An existing window cannot be silently enlarged through a new invocation.
            db.execute("UPDATE budget SET cap = min(cap, ?) WHERE id = 1", (units(limit_cny),))
            db.execute("CREATE TABLE IF NOT EXISTS calls "
                       "(id TEXT PRIMARY KEY, run_id TEXT, reserved INTEGER, charged INTEGER, status TEXT)")

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def reserve(self, run_id: str, estimate_cny: float) -> str:
        amount, call_id = units(estimate_cny), uuid4().hex
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            cap = db.execute("SELECT cap FROM budget WHERE id = 1").fetchone()[0]
            used = db.execute("SELECT coalesce(sum(coalesce(charged, reserved)), 0) FROM calls").fetchone()[0]
            if used + amount > cap:
                raise RunLimit("cumulative_cost_budget")
            db.execute("INSERT INTO calls VALUES (?, ?, ?, NULL, 'reserved')", (call_id, run_id, amount))
        return call_id

    def settle(self, call_id: str, cost_cny: float | None) -> None:
        with closing(self.connect()) as db, db:
            db.execute("UPDATE calls SET charged = ?, status = ? WHERE id = ? AND status = 'reserved'",
                       (None if cost_cny is None else units(cost_cny),
                        "unknown" if cost_cny is None else "estimated", call_id))

    def snapshot(self, run_id: str | None = None) -> dict:
        with closing(self.connect()) as db:
            cap = db.execute("SELECT cap FROM budget WHERE id = 1").fetchone()[0]
            rows = db.execute("SELECT run_id, reserved, charged, status FROM calls").fetchall()
        selected = [r for r in rows if run_id is None or r[0] == run_id]
        return {
            "cap_cny": cap / 1_000_000,
            "accounted_cny": sum(r[1] if r[2] is None else r[2] for r in selected) / 1_000_000,
            "uncertain_calls": sum(r[3] in {"reserved", "unknown"} for r in selected),
            "calls": len(selected),
        }
