"""固定预算的检索对照；不使用 LLM 裁判，严格评分由人工核对 Oracle。"""
import argparse
import hashlib
import json
import re
from pathlib import Path

import yaml

from repopilot.agent_runner import run_agent, _run_id
from repopilot.agent.trace import save_trace
from repopilot.config import PROJECT_ROOT, load_agent_settings


def measure_run(run_dir: Path, oracle: dict) -> dict:
    """只统计已回传的工具结果，不能把索引找到但未输出的行算命中。"""
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    runtime = json.loads((run_dir / "runtime.json").read_text(encoding="utf-8"))
    starts, observed, counts = {}, [], {}
    output_chars = 0
    for line in (run_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        data = event["data"]
        if event["event"] == "tool_call_started":
            starts[data["tool_call_id"]] = data
            counts[data["tool"]] = counts.get(data["tool"], 0) + 1
        elif event["event"] == "tool_call_completed":
            result = data["result"]
            output_chars += len(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
            if not result["ok"]:
                continue
            tool = result["tool"]
            payload = result["data"]
            if tool == "search_code":
                observed.extend((m["path"], m["line"]) for m in payload["matches"])
            elif tool in {"read_file", "search_symbols"}:
                path = None
                if tool == "read_file":
                    args = json.loads(starts[data["tool_call_id"]]["arguments_json"])
                    path = Path(args["path"]).as_posix()
                for text_line in payload.get("content", payload.get("context", "")).splitlines():
                    header = re.match(r"^(.+):(?:\d+-\d+|相关文件1-20预览)$", text_line)
                    if tool == "search_symbols" and header:
                        path = header.group(1)
                    numbered = re.match(r"^(\d+): ", text_line)
                    if path and numbered:
                        observed.append((path, int(numbered.group(1))))
    unique = set(observed)
    targets = oracle.get("evidence_targets", [])
    target_lines = {(t["path"], n) for t in targets
                    for n in range(t["start_line"], t["end_line"] + 1)}
    hits = sum(any((t["path"], n) in unique for n in range(t["start_line"], t["end_line"] + 1))
               for t in targets)
    return {
        "status": summary["status"], "stop_reason": summary["stop_reason"],
        "model_steps": summary["model_steps"], "tool_calls": summary["tool_calls"],
        "total_tokens": summary["usage"]["total_tokens"],
        "estimated_cost_cny": summary["estimated_cost_cny"],
        "loop_seconds": summary["elapsed_seconds"], "index_seconds": runtime["index_seconds"],
        "tool_counts": counts, "tool_output_chars": output_chars,
        "observed_source_lines": len(observed), "unique_source_lines": len(unique),
        "repeated_source_line_fraction": 1 - len(unique) / len(observed) if observed else 0,
        "evidence_ranges_hit": hits if targets else None, "evidence_ranges_total": len(targets),
        "evidence_line_coverage": len(unique & target_lines) / len(target_lines) if target_lines else None,
        "strict_pass": None, "score": None, "review_notes": "待人工按原 Oracle 核对",
    }


def compare(task_ids: list[str] | None = None, cost_limit: float = 10.0) -> Path:
    settings = load_agent_settings()
    tasks = sorted(task_ids or [p.name for p in settings.tasks_dir.iterdir() if p.is_dir()])
    # 在付费调用前校验所有任务，冻结完整计划和源码哈希。
    for task_id in tasks:
        if task_id not in {p.name for p in settings.tasks_dir.iterdir() if p.is_dir()}:
            raise ValueError(f"未知任务: {task_id}")
    root = settings.runs_dir / "comparison" / _run_id()
    source_paths = [p for folder in ("repopilot", "scripts")
                    for p in (PROJECT_ROOT / folder).rglob("*.py")]
    repo_paths = [p for repo in {yaml.safe_load((settings.tasks_dir / t / 'task.yaml').read_text(encoding='utf-8'))['repository']['baseline_path'] for t in tasks}
                  for p in (PROJECT_ROOT / repo).rglob("*.py") if "__pycache__" not in p.parts]
    hashes = {p.relative_to(PROJECT_ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in source_paths + repo_paths}
    plan = [(task, mode) for i, task in enumerate(tasks)
            for mode in (("code", "symbols") if i % 2 == 0 else ("symbols", "code"))]
    save_trace(root / "manifest.json", {
        "plan": plan, "repetitions": 1, "estimated_cost_limit_cny": cost_limit,
        "config": yaml.safe_load((PROJECT_ROOT / "configs/agent.yaml").read_text(encoding="utf-8")),
        "source_sha256": hashes, "evaluation": "existing_oracle_manual_review",
    })
    records, total_cost = [], 0.0
    for task, mode in plan:
        if total_cost >= cost_limit:
            print("达到整批估算费用检查线，剩余任务未运行", flush=True)
            break
        print(f"对照: {task} / {mode}", flush=True)
        row = {"task_id": task, "mode": mode}
        try:
            exit_code = run_agent(task_ids=[task], repetitions=1, retrieval_mode=mode,
                                  output_dir=root / mode)
            run_dir = root / mode / task / "run-01"
            row["exit_code"] = exit_code
            row["run_dir"] = str(run_dir)
            if not (run_dir / "summary.json").exists():
                row.update(status="infrastructure_failure", strict_pass=False,
                           review_notes="无 summary，保留失败记录；费用未知，停止整批")
            else:
                oracle = yaml.safe_load((settings.tasks_dir / task / "oracle.yaml").read_text(encoding="utf-8"))
                row.update(measure_run(run_dir, oracle))
                total_cost += row["estimated_cost_cny"]
        except Exception as exc:
            row.update(status="infrastructure_failure", strict_pass=False,
                       review_notes=f"异常 {type(exc).__name__}；费用未知，停止整批")
        records.append(row)
        save_trace(root / "metrics.json", {"estimated_cost_cny": total_cost, "records": records,
                                          "planned_runs": len(plan), "finished_runs": len(records)})
        if row["status"] == "infrastructure_failure":
            break
    print(f"对照结果: {root}", flush=True)
    return root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", action="append", dest="tasks")
    parser.add_argument("--cost-limit", type=float, default=10.0)
    args = parser.parse_args()
    if args.cost_limit <= 0:
        parser.error("cost-limit 必须大于 0")
    compare(args.tasks, args.cost_limit)


if __name__ == "__main__":
    main()
