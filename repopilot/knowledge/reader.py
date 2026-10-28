"""离线报告使用的只读历史正文入口；不建库、不迁移、不同步当前索引。"""

from pathlib import Path
import sqlite3

from .documents import KnowledgeFault
from .store import KnowledgeStore


class KnowledgeReader(KnowledgeStore):
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.path = self.root / "knowledge.sqlite3"

    def connect(self):
        try:
            db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=15)
        except sqlite3.DatabaseError:
            raise KnowledgeFault("knowledge_store_unavailable") from None
        db.row_factory = sqlite3.Row
        return db
