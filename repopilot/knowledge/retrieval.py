"""有界词项检索与版本固定的资料清单；不采用向量服务。"""

from contextlib import closing
import json

from repopilot.memory.facts import repository_id
from .documents import KnowledgeFault, digest, terms


def manifest(store, repository, scope="repository"):
    store.sync(repository)
    relevant = {r["id"]: r for r in store.records(repository)
                if json.loads(r["metadata"])["scope"] in {"repository", scope}}
    inherited = {}
    # 替代必需规则不能悄悄解除其必需性；后继失效时同样停止。
    for identifier, record in relevant.items():
        if not json.loads(record["metadata"])["required"]:
            continue
        current, seen = identifier, set()
        while relevant[current]["status"] == "superseded":
            if current in seen:
                raise KnowledgeFault("knowledge_relation_cycle")
            seen.add(current)
            current = json.loads(relevant[current]["confirmation"] or "{}").get("replacement")
            if current not in relevant:
                raise KnowledgeFault("knowledge_required_unconfirmed")
        if current != identifier:
            inherited.setdefault(current, []).append(identifier)
    entries = []
    for record in relevant.values():
        metadata = json.loads(record["metadata"])
        if record["status"] == "superseded":
            continue
        if record["id"] in inherited:
            metadata.update(required=True, required_by=inherited[record["id"]])
        if metadata["required"] and record["status"] != "confirmed":
            raise KnowledgeFault("knowledge_required_unconfirmed")
        if record["status"] == "confirmed":
            entries.append({**metadata, "version": record["version"], "origin": record["origin"],
                            "path": record["path"], "status": "confirmed"})
    snapshot = {"repository_id": repository_id(repository), "entries": entries}
    return snapshot | {"snapshot_id": digest(snapshot)}


def search(store, repository, query, *, scope="repository", proposed=False, limit=5, max_bytes=6000):
    store.sync(repository)
    words = sorted(set(terms(query)))[:32]
    if not words:
        return {"entries": [], "max_bytes": max_bytes}
    expression = " OR ".join('"' + word + '"' for word in words)
    with closing(store.connect()) as db:
        rows = db.execute(
            "SELECT c.*,m.version,m.metadata,m.path,bm25(material_search) AS rank "
            "FROM material_search JOIN material_chunks c ON c.rowid=material_search.rowid "
            "JOIN materials m ON m.repository=c.repository AND m.id=c.id "
            "WHERE material_search MATCH ? AND c.repository=? AND "
            "(m.status='confirmed' OR (? AND m.status='proposed')) "
            "AND json_extract(m.metadata,'$.scope') IN ('repository',?) "
            "ORDER BY rank,c.id,c.start_line LIMIT 40",
            (expression, repository_id(repository), proposed, scope)).fetchall()
    entries, omitted = [], []
    for row in rows:
        # 兼容旧索引中的长块，不要求修改正文或重建全库才能恢复检索。
        for start in range(row["start_line"], row["end_line"] + 1, 200):
            try:
                entry = store.read(repository, row["id"], version=row["version"],
                                   start_line=start, end_line=min(start + 199, row["end_line"]), include_events=False)
            except KnowledgeFault as exc:
                if str(exc) != "knowledge_excerpt_too_large":
                    raise
                omitted.append({"id": row["id"], "reason": str(exc)})
                continue
            entry.update(path=row["path"], heading=row["heading"], kind=json.loads(row["metadata"])["kind"])
            if len(entries) < limit and len(json.dumps(entries + [entry], ensure_ascii=False).encode()) <= max_bytes:
                entries.append(entry)
    return {"entries": entries, "omitted": omitted, "max_bytes": max_bytes}


def related(store, repository, identifier, relations=None):
    record = store.inspect(repository, identifier)
    result = []
    for relation, identifiers in (record["relations"] if relations is None else relations).items():
        for target in identifiers:
            try:
                status = store.inspect(repository, target)["status"]
            except KnowledgeFault:
                status = "missing"
            result.append({"relation": relation, "target": target, "status": status})
    return result
