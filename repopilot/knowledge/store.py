"""资料登记与增量FTS索引，正文和历史版本保存在普通UTF-8文件中。"""

from contextlib import closing
import json
from pathlib import Path
import sqlite3

from repopilot.execution.files import Repository
from repopilot.execution.workspace import head_commit
from repopilot.memory.facts import repository_id
from repopilot.storage.jobs import timestamp
from .documents import Material, KnowledgeFault, atomic_text, checked_body, chunks, digest, terms
from . import events


class KnowledgeStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "knowledge.sqlite3"
        with closing(self.connect()) as db, db:
            schema = db.execute("PRAGMA user_version").fetchone()[0]
            if schema not in {0, 1, 2}:
                raise KnowledgeFault("knowledge_schema_unsupported")
            db.execute("CREATE TABLE IF NOT EXISTS materials (repository TEXT NOT NULL, id TEXT NOT NULL, "
                       "origin TEXT NOT NULL, path TEXT NOT NULL, metadata TEXT NOT NULL, version TEXT, "
                       "status TEXT NOT NULL DEFAULT 'proposed', confirmation TEXT, revision INTEGER NOT NULL DEFAULT 0, "
                       "PRIMARY KEY(repository,id), UNIQUE(repository,origin,path))")
            db.execute("CREATE TABLE IF NOT EXISTS material_versions (repository TEXT NOT NULL,id TEXT NOT NULL, "
                       "version TEXT NOT NULL,path TEXT NOT NULL,metadata TEXT NOT NULL,observed_commit TEXT, "
                       "created_at TEXT NOT NULL,PRIMARY KEY(repository,id,version))")
            db.execute("CREATE TABLE IF NOT EXISTS material_chunks (rowid INTEGER PRIMARY KEY,repository TEXT NOT NULL, "
                       "id TEXT NOT NULL,chunk_key TEXT NOT NULL,heading TEXT NOT NULL,start_line INTEGER NOT NULL, "
                       "end_line INTEGER NOT NULL,UNIQUE(repository,id,chunk_key))")
            db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS material_search USING fts5(terms,title)")
            events.initialize(db, schema)

    def connect(self):
        db = None
        try:
            db = sqlite3.connect(self.path, timeout=15)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA schema_version").fetchone()
            return db
        except sqlite3.DatabaseError:
            if db is not None:
                db.close()
            raise KnowledgeFault("knowledge_store_unavailable") from None

    def _local(self, relative):
        try:
            return Repository(self.root).resolve(str(relative), must_exist=False)
        except ValueError:
            raise KnowledgeFault("knowledge_path_denied") from None

    def records(self, repository):
        with closing(self.connect()) as db:
            return [dict(row) for row in db.execute("SELECT * FROM materials WHERE repository=? ORDER BY id",
                                                   (repository_id(repository),))]

    def get(self, repository, identifier):
        with closing(self.connect()) as db:
            row = db.execute("SELECT * FROM materials WHERE repository=? AND id=?",
                             (repository_id(repository), identifier)).fetchone()
        if row is None:
            raise KnowledgeFault("knowledge_not_found")
        return dict(row)

    def register(self, repository, path, material, *, origin="repository"):
        repository = Path(repository)
        material = Material.model_validate(material)
        namespace = repository_id(repository)
        if origin == "repository":
            path = Repository(repository).resolve(path).relative_to(Path(repository).resolve()).as_posix()
            checked_body(Repository(repository).text(path))
        elif origin != "managed" or path != f"{material.id}.md":
            raise KnowledgeFault("knowledge_origin_invalid")
        encoded = material.model_dump_json()
        checked_body(encoded)
        with closing(self.connect()) as db, db:
            db.execute("INSERT INTO materials(repository,id,origin,path,metadata) VALUES (?,?,?,?,?) "
                       "ON CONFLICT(repository,id) DO UPDATE SET origin=excluded.origin,path=excluded.path, "
                       "metadata=excluded.metadata,revision=materials.revision+1 "
                       "WHERE origin!=excluded.origin OR path!=excluded.path OR metadata!=excluded.metadata",
                       (namespace, material.id, origin, path, encoded))
        self.sync(repository, identifiers=[material.id])
        return self.inspect(repository, material.id)

    def record(self, repository, text, material, *, secrets=()):
        material = Material.model_validate(material)
        text = checked_body(text, secrets)
        namespace = repository_id(repository)
        path = self._local(Path("knowledge") / namespace / "documents" / f"{material.id}.md")
        existing = next((r for r in self.records(repository) if r["id"] == material.id), None)
        checked_body(material.model_dump_json(), secrets)
        if existing and (existing["origin"] != "managed" or existing["status"] == "superseded"):
            raise KnowledgeFault("knowledge_record_identity_conflict")
        if existing and existing["status"] == "confirmed":
            if self._body(repository, existing) != text or existing["metadata"] != material.model_dump_json():
                raise KnowledgeFault("knowledge_confirmed_record_immutable")
        atomic_text(path, text)
        return self.register(repository, f"{material.id}.md", material, origin="managed")

    def _body(self, repository, record):
        repository = Path(repository)
        if record["origin"] == "repository":
            path = Repository(repository).resolve(record["path"], must_exist=False)
            if not path.exists():
                return None
            return checked_body(Repository(repository).text(record["path"]))
        path = self._local(Path("knowledge") / record["repository"] / "documents" / record["path"])
        return None if not path.exists() else checked_body(path.read_text(encoding="utf-8"))

    def sync(self, repository, *, identifiers=None):
        plans, unchanged = [], []
        for record in self.records(repository):
            if identifiers is not None and record["id"] not in identifiers:
                continue
            if record["status"] == "superseded":
                continue
            body = self._body(repository, record)
            version = None if body is None else digest([record["origin"], record["path"], record["metadata"], body])
            if version == record["version"]:
                unchanged.append(record["id"])
                continue
            parts = [] if body is None else chunks(body)
            snapshot = None
            if body is not None:
                snapshot = Path("knowledge") / record["repository"] / "versions" / f"{version}.md"
                atomic_text(self._local(snapshot), body)
            plans.append((record, version, snapshot, parts))
        # 文件写入先于事务，失败最多留下未引用文件；索引整体回滚，不出现半次同步。
        observed_commit = head_commit(Path(repository)) if plans else None
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            for record, version, snapshot, parts in plans:
                changed = db.execute("UPDATE materials SET version=?,status=?,confirmation=NULL,revision=revision+1 "
                    "WHERE repository=? AND id=? AND revision=?", (version, "deleted" if version is None else "proposed",
                    record["repository"], record["id"], record["revision"]))
                if changed.rowcount != 1:
                    raise KnowledgeFault("knowledge_sync_conflict")
                if record["version"] is not None:
                    events.append(db, record, "invalidated", actor="system", source="sync",
                        reason="source_deleted" if version is None else "source_changed", next_version=version)
                if version is not None:
                    provenance = json.dumps({"material": json.loads(record["metadata"]),
                        "metadata_json": record["metadata"], "origin": record["origin"], "path": record["path"]})
                    db.execute("INSERT OR IGNORE INTO material_versions VALUES (?,?,?,?,?,?,?)",
                        (record["repository"], record["id"], version, snapshot.as_posix(), provenance,
                         observed_commit, timestamp()))
                self._index(db, record, parts)
        return {"updated": [p[0]["id"] for p in plans], "unchanged": unchanged}

    @staticmethod
    def _index(db, record, parts):
        keys = {p["key"] for p in parts}
        old = {r["chunk_key"]: dict(r) for r in db.execute(
            "SELECT * FROM material_chunks WHERE repository=? AND id=?", (record["repository"], record["id"]))}
        for key, row in old.items():
            if key not in keys:
                db.execute("DELETE FROM material_search WHERE rowid=?", (row["rowid"],))
                db.execute("DELETE FROM material_chunks WHERE rowid=?", (row["rowid"],))
        title = " ".join(terms(json.loads(record["metadata"])["title"]))
        for part in parts:
            if part["key"] in old:
                db.execute("UPDATE material_chunks SET heading=?,start_line=?,end_line=? WHERE rowid=?",
                    (part["heading"], part["start_line"], part["end_line"], old[part["key"]]["rowid"]))
                db.execute("UPDATE material_search SET title=? WHERE rowid=? AND title!=?",
                           (title, old[part["key"]]["rowid"], title))
                continue
            row = db.execute("INSERT INTO material_chunks(repository,id,chunk_key,heading,start_line,end_line) "
                "VALUES (?,?,?,?,?,?)", (record["repository"], record["id"], part["key"], part["heading"],
                                        part["start_line"], part["end_line"]))
            db.execute("INSERT INTO material_search(rowid,terms,title) VALUES (?,?,?)",
                       (row.lastrowid, part["terms"], title))

    def confirm(self, repository, identifier, *, version, reason, source="cli"):
        if not reason.strip():
            raise KnowledgeFault("knowledge_reason_required")
        self.sync(repository, identifiers=[identifier])
        record = self.get(repository, identifier)
        if record["version"] != version or record["status"] in {"deleted", "superseded"}:
            raise KnowledgeFault("knowledge_version_changed")
        confirmation = json.dumps({"actor": "local-operator", "version": version,
                                   "reason": reason, "at": timestamp()}, ensure_ascii=False)
        with closing(self.connect()) as db, db:
            changed = db.execute("UPDATE materials SET status='confirmed',confirmation=?,revision=revision+1 "
                "WHERE repository=? AND id=? AND revision=?", (confirmation, record["repository"], identifier, record["revision"]))
            if changed.rowcount != 1:
                raise KnowledgeFault("knowledge_sync_conflict")
            events.append(db, record, "confirmed", source=source, **json.loads(confirmation))
        return self.inspect(repository, identifier)

    def supersede(self, repository, identifier, *, replacement, reason):
        if identifier == replacement or not reason.strip():
            raise KnowledgeFault("knowledge_supersession_invalid")
        record = self.get(repository, identifier)
        if self.inspect(repository, replacement)["status"] != "confirmed":
            raise KnowledgeFault("knowledge_replacement_unconfirmed")
        with closing(self.connect()) as db, db:
            confirmation = {"replacement": replacement, "reason": reason,
                            "actor": "local-operator", "at": timestamp()}
            changed = db.execute("UPDATE materials SET status='superseded',confirmation=?,revision=revision+1 "
                "WHERE repository=? AND id=? AND revision=?", (json.dumps(confirmation),
                record["repository"], identifier, record["revision"]))
            if changed.rowcount != 1:
                raise KnowledgeFault("knowledge_sync_conflict")
            events.append(db, record, "superseded", **confirmation)
        return self.inspect(repository, identifier)

    def history(self, repository, identifier, *, version=None):
        record = self.get(repository, identifier)
        with closing(self.connect()) as db:
            return events.history(db, record["repository"], identifier, version)

    def inspect(self, repository, identifier):
        record = self.get(repository, identifier)
        return {**json.loads(record["metadata"]), "origin": record["origin"], "path": record["path"],
                "version": record["version"], "status": record["status"],
                "confirmation": None if record["confirmation"] is None else json.loads(record["confirmation"])}

    def read(self, repository, identifier, *, version, start_line=1, end_line=120, verify_only=False,
             include_events=True):
        if not 1 <= start_line <= end_line or end_line - start_line >= 200:
            raise KnowledgeFault("knowledge_line_range_invalid")
        with closing(self.connect()) as db:
            row = db.execute("SELECT * FROM material_versions WHERE repository=? AND id=? AND version=?",
                             (repository_id(repository), identifier, version)).fetchone()
        if row is None:
            raise KnowledgeFault("knowledge_version_not_found")
        path = self._local(row["path"])
        try:
            text = checked_body(path.read_text(encoding="utf-8"))
        except OSError:
            raise KnowledgeFault("knowledge_snapshot_unavailable") from None
        provenance = json.loads(row["metadata"])
        metadata = provenance["material"]
        origin = self.get(repository, identifier)
        expected = digest([provenance["origin"], provenance["path"], provenance["metadata_json"], text])
        if expected != version:
            raise KnowledgeFault("knowledge_snapshot_corrupt")
        if verify_only:
            return {"id": identifier, "version": version}
        lines = text.splitlines()
        if start_line > len(lines):
            raise KnowledgeFault("knowledge_line_range_invalid")
        excerpt = "\n".join(lines[start_line - 1:end_line])
        if len(excerpt.encode("utf-8")) > 8000:
            raise KnowledgeFault("knowledge_excerpt_too_large")
        return {"id": identifier, "version": version, "title": metadata["title"], "text": excerpt,
                "path": provenance["path"], "origin": provenance["origin"],
                "start_line": start_line, "end_line": min(end_line, len(lines)), "total_lines": len(lines),
                "observed_commit": row["observed_commit"], "sources": metadata["sources"],
                "relations": metadata["relations"],
                **({"events": self.history(repository, identifier, version=version)} if include_events else {}),
                "status": "historical" if origin["version"] != version else origin["status"]}
