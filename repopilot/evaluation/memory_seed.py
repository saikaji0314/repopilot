"""评测用的显式源码书签输入；默认基准仍拒绝未声明的仓库记忆。"""

import json
from pathlib import Path

from repopilot.evaluation.swebench import digest
from repopilot.memory.facts import capture, repository_id
from repopilot.memory.store import MemoryStore
from repopilot.orchestration.state import canonical_hash


def validate_seed(path, instance_id, repository, base_commit):
    raw = Path(path).read_bytes()
    if len(raw) > 64000:
        raise ValueError("benchmark_memory_manifest_too_large")
    data = json.loads(raw)
    if (set(data) != {"schema_version", "kind", "provenance", "cases"} or
            data["schema_version"] != "1.0" or data["kind"] != "operator_prepared_source_bookmarks"):
        raise ValueError("benchmark_memory_manifest_invalid")
    matches = [case for case in data["cases"] if case.get("instance_id") == instance_id]
    if len(matches) != 1:
        raise ValueError("benchmark_memory_case_missing_or_duplicate")
    case = matches[0]
    if (set(case) != {"instance_id", "base_commit", "entries"} or case["base_commit"] != base_commit or
            not 1 <= len(case["entries"]) <= 5):
        raise ValueError("benchmark_memory_base_mismatch")
    facts = []
    for entry in case["entries"]:
        if set(entry) != {"path", "start_line", "end_line", "content_sha256"}:
            raise ValueError("benchmark_memory_entry_invalid")
        parts = Path(entry["path"]).parts
        if "tests" in parts or Path(entry["path"]).name.startswith("test_"):
            raise ValueError("benchmark_memory_tests_denied")
        fact = capture(repository, entry["path"], entry["start_line"], entry["end_line"])
        if fact["source_commit"] != base_commit or digest(fact["content"].encode()) != entry["content_sha256"]:
            raise ValueError("benchmark_memory_source_mismatch")
        facts.append(fact)
    if len({(f["path"], f["start_line"], f["end_line"]) for f in facts}) != len(facts):
        raise ValueError("benchmark_memory_duplicate_entry")
    return {"manifest_sha256": digest(raw), "instance_id": instance_id,
            "base_commit": base_commit, "facts": facts}


def prepare_seed(validated, repository, runtime_root):
    store = MemoryStore(runtime_root)
    # 先检查现有 active 全集，不能让先前候选或其他实验事实混入本次输入。
    expected = {(f["path"], f["start_line"], f["end_line"]) for f in validated["facts"]}
    active = store.records(repository, status="active")
    if any((r["path"], r["start_line"], r["end_line"]) not in expected or
           r["source_run"] is not None or r["source_commit"] != validated["base_commit"] for r in active):
        raise ValueError("benchmark_memory_unfrozen_active_facts")
    records = [store.remember(repository, f["path"], f["start_line"], f["end_line"])
               for f in validated["facts"]]
    if any(r["status"] != "active" for r in records):
        raise ValueError("benchmark_memory_seed_not_active")
    ids = sorted(r["id"] for r in records)
    if sorted(r["id"] for r in store.records(repository, status="active")) != ids:
        raise ValueError("benchmark_memory_unfrozen_active_facts")
    return {"manifest_sha256": validated["manifest_sha256"], "base_commit": validated["base_commit"],
            "repository_id": repository_id(repository), "active_ids": ids,
            "seed_kind": "operator_prepared_source_bookmarks"}


def seed_snapshot(binding, repository, runtime_root, query):
    snapshot = MemoryStore(runtime_root).search(repository, query)
    if any(entry["id"] not in binding["active_ids"] for entry in snapshot["entries"]):
        raise ValueError("benchmark_memory_snapshot_not_bound")
    snapshot["snapshot_id"] = canonical_hash(snapshot)
    return snapshot
