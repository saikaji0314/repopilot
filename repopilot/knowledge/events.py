"""与当前状态同事务追加人工决定及失效事件；不将旧正文误当旧决定。"""

import json

from repopilot.storage.jobs import timestamp


def initialize(db, schema):
    db.execute("CREATE TABLE IF NOT EXISTS material_events (sequence INTEGER PRIMARY KEY, "
               "repository TEXT NOT NULL,id TEXT NOT NULL,version TEXT,event TEXT NOT NULL,payload TEXT NOT NULL)")
    db.execute("CREATE INDEX IF NOT EXISTS material_events_lookup ON material_events(repository,id,version,sequence)")
    if schema == 1:
        # 旧库仅能补录仍存于当前行的决定；已被旧程序覆盖的历史无法凭空恢复。
        for row in db.execute("SELECT * FROM materials WHERE confirmation IS NOT NULL").fetchall():
            data = json.loads(row["confirmation"])
            append(db, row, "superseded" if row["status"] == "superseded" else "confirmed",
                   source="legacy_migration", **data)
    db.execute("PRAGMA user_version=2")


def append(db, record, event, *, source="cli", **details):
    payload = {"actor": "local-operator", "source": source, "at": timestamp(), **details}
    db.execute("INSERT INTO material_events(repository,id,version,event,payload) VALUES (?,?,?,?,?)",
               (record["repository"], record["id"], record["version"], event,
                json.dumps(payload, ensure_ascii=False)))


def history(db, repository, identifier, version=None):
    rows = db.execute("SELECT * FROM material_events WHERE repository=? AND id=? "
                      "AND (? IS NULL OR version=?) ORDER BY sequence",
                      (repository, identifier, version, version))
    return [{"sequence": row["sequence"], "version": row["version"], "event": row["event"],
             **json.loads(row["payload"])} for row in rows]
