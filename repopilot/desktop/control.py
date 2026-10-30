"""A cooperative pause request, acknowledged only after a safe phase checkpoint."""

from contextlib import closing
from pathlib import Path
import sqlite3


def connect(root: Path):
    db = sqlite3.connect(Path(root) / 'desktop.sqlite3', timeout=15)
    db.execute('CREATE TABLE IF NOT EXISTS pause_requests (run_id TEXT PRIMARY KEY)')
    return db


def request_pause(root: Path, run_id: str):
    with closing(connect(root)) as db, db:
        db.execute('INSERT OR IGNORE INTO pause_requests VALUES (?)', (run_id,))


def pause_pending(root: Path, run_id: str):
    if not (Path(root) / 'desktop.sqlite3').exists():
        return False
    with closing(connect(root)) as db:
        return bool(db.execute('SELECT 1 FROM pause_requests WHERE run_id=?', (run_id,)).fetchone())


def consume_pause(root: Path, run_id: str):
    if not (Path(root) / 'desktop.sqlite3').exists():
        return False
    with closing(connect(root)) as db, db:
        return db.execute('DELETE FROM pause_requests WHERE run_id=?', (run_id,)).rowcount > 0
