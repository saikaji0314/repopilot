"""单次 LLM 基线：固定任务输入、无工具、结构化输出。"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
import traceback
import uuid

from pydantic import ValidationError

from repopilot.config import BaselineSettings, load_settings
from repopilot.llm_client import call_llm, create_client
from repopilot.repository_snapshot import collect_source_files
from repopilot.schemas import BaselineAnswer
from repopilot.task_loader import get_task_user_prompt, parse_task
from repopilot.verification import verify_clean_repository, verify_task_defect
from repopilot.workspace import prepared_task_workspace
from repopilot.repository_runtime import resolve_repository


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{uuid.uuid4().hex[:8]}"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _select_task_dirs(settings: BaselineSettings, task_ids: list[str] | None) -> list[Path]:
    all_dirs = sorted(path for path in settings.tasks_dir.iterdir() if path.is_dir())
    by_id = {path.name: path for path in all_dirs}
    if task_ids is None:
        return all_dirs

    unknown = sorted(set(task_ids) - by_id.keys())
    if unknown:
        raise ValueError(f"未知任务 ID: {unknown}")
    return [by_id[task_id] for task_id in task_ids]


def _usage_dict(completion) -> dict[str, int]:
    usage = completion.usage
    if usage is None:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    return {
        "prompt_tokens": int(usage.prompt_tokens or 0),
        "completion_tokens": int(usage.completion_tokens or 0),
        "total_tokens": int(usage.total_tokens or 0),
    }


def _estimate_cost(usage: dict[str, int], settings: BaselineSettings) -> float:
    return round(
        usage["prompt_tokens"] * settings.input_price_per_million / 1_000_000
        + usage["completion_tokens"]
        * settings.output_price_per_million
        / 1_000_000,
        8,
    )


def _build_prompt(task: dict, source_snapshot: str) -> str:
    return (
        get_task_user_prompt(task)
        + "\n## 仓库快照\n\n"
        + "--- BEGIN REPOSITORY SNAPSHOT ---\n"
        + source_snapshot
        + "\n--- END REPOSITORY SNAPSHOT ---\n"
    )


def run_baseline(
    *,
    task_ids: list[str] | None,
    dry_run: bool,
    repetitions: int | None = None,
) -> dict:
    settings = load_settings(require_credentials=not dry_run)
    selected_dirs = _select_task_dirs(settings, task_ids)
    repeat_count = repetitions or settings.repetitions_per_task
    if repeat_count < 1:
        raise ValueError("repetitions 必须大于等于 1")

    checked_repositories = set()

    client = None if dry_run else create_client(settings)
    current_run_id = _run_id()
    run_root = settings.runs_dir / current_run_id
    summary: dict = {
        "run_id": current_run_id,
        "started_at": _utc_now(),
        "dry_run": dry_run,
        "model": settings.model,
        "repetitions_per_task": 0 if dry_run else repeat_count,
        "tasks": [],
    }
    if not dry_run:
        run_root.mkdir(parents=True, exist_ok=False)

    for task_dir in selected_dirs:
        task_summary: dict = {"task_id": task_dir.name, "runs": []}
        print(f"\n执行任务: {task_dir.name}")
        try:
            task = parse_task(task_dir / "task.yaml")
            if task["id"] != task_dir.name:
                raise ValueError(
                    f"任务目录名与 task.yaml.id 不一致: {task_dir.name} != {task['id']}"
                )

            repo, python = resolve_repository(task, settings.repo_dir)
            if (repo, python) not in checked_repositories:
                clean_result = verify_clean_repository(
                    repo, timeout_seconds=settings.timeout_seconds, python_executable=python
                )
                print(f"干净基线 {repo.name}: {clean_result.stdout.strip().splitlines()[-1]}")
                checked_repositories.add((repo, python))
            with prepared_task_workspace(repo, task_dir, task) as workspace:
                defect_result = verify_task_defect(task, workspace, python_executable=python)
                source_snapshot = collect_source_files(workspace)
                if not source_snapshot.strip():
                    raise RuntimeError(f"任务 {task['id']} 的仓库快照为空")
                prompt = _build_prompt(task, source_snapshot)

                print(
                    f"  本地预检通过；snapshot={len(source_snapshot)} chars，"
                    f"prompt={len(prompt)} chars"
                )
                if dry_run:
                    task_summary["status"] = "dry_run_ok"
                    task_summary["defect_returncode"] = defect_result.returncode
                    task_summary["snapshot_sha256"] = _sha256(source_snapshot)
                    task_summary["prompt_sha256"] = _sha256(prompt)
                    summary["tasks"].append(task_summary)
                    continue

                task_result_dir = run_root / task["id"]
                task_result_dir.mkdir(parents=True, exist_ok=False)
                (task_result_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
                _write_json(task_result_dir / "preflight.json", defect_result.to_dict())

                for run_number in range(1, repeat_count + 1):
                    repetition_dir = task_result_dir / f"run-{run_number:02d}"
                    repetition_dir.mkdir(parents=True, exist_ok=False)
                    started = time.perf_counter()
                    record: dict = {
                        "task_id": task["id"],
                        "run_number": run_number,
                        "model": settings.model,
                        "timestamp": _utc_now(),
                        "prompt_sha256": _sha256(prompt),
                        "snapshot_sha256": _sha256(source_snapshot),
                    }
                    try:
                        completion = call_llm(client, settings, prompt)
                        elapsed = time.perf_counter() - started
                        raw_response = json.loads(completion.model_dump_json())
                        _write_json(repetition_dir / "raw_response.json", raw_response)

                        answer_text = completion.choices[0].message.content
                        if not answer_text:
                            raise ValueError("模型返回空 content")
                        (repetition_dir / "answer.txt").write_text(
                            answer_text, encoding="utf-8"
                        )

                        usage = _usage_dict(completion)
                        record.update(
                            {
                                "elapsed_seconds": round(elapsed, 4),
                                "usage": usage,
                                "estimated_cost_cny": _estimate_cost(usage, settings),
                            }
                        )
                        try:
                            answer = BaselineAnswer.model_validate_json(answer_text)
                        except ValidationError as exc:
                            record["status"] = "format_error"
                            record["error"] = json.loads(exc.json())
                        else:
                            record["status"] = "success"
                            _write_json(
                                repetition_dir / "answer.json",
                                answer.model_dump(mode="json"),
                            )
                    except Exception as exc:
                        record.update(
                            {
                                "status": "api_or_response_error",
                                "elapsed_seconds": round(
                                    time.perf_counter() - started, 4
                                ),
                                "error": {
                                    "type": type(exc).__name__,
                                    "message": str(exc),
                                },
                            }
                        )

                    _write_json(repetition_dir / "meta.json", record)
                    task_summary["runs"].append(record)
                    print(
                        f"  run-{run_number:02d}: {record['status']}, "
                        f"cost={record.get('estimated_cost_cny', 0):.6f} 元"
                    )

                task_summary["status"] = (
                    "success"
                    if all(run["status"] == "success" for run in task_summary["runs"])
                    else "failed"
                )
        except Exception as exc:
            task_summary.update(
                {
                    "status": "preflight_error",
                    "error": {"type": type(exc).__name__, "message": str(exc)},
                }
            )
            traceback.print_exc()
            if not dry_run:
                _write_json(run_root / task_dir.name / "error.json", task_summary)

        summary["tasks"].append(task_summary)

    summary["finished_at"] = _utc_now()
    summary["total_estimated_cost_cny"] = round(
        sum(
            run.get("estimated_cost_cny", 0)
            for task in summary["tasks"]
            for run in task.get("runs", [])
        ),
        8,
    )
    if not dry_run:
        _write_json(run_root / "summary.json", summary)

    ok_statuses = {"dry_run_ok"} if dry_run else {"success"}
    successful = sum(task.get("status") in ok_statuses for task in summary["tasks"])
    print(f"\n完成: {successful}/{len(summary['tasks'])} 个任务成功")
    if not dry_run:
        print(f"结果目录: {run_root}")
        print(f"估算总费用: {summary['total_estimated_cost_cny']:.6f} 元")
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="运行 RepoPilot 单次 LLM 基线")
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--all", action="store_true", help="运行全部任务")
    selection.add_argument(
        "--task",
        action="append",
        dest="task_ids",
        metavar="TASK_ID",
        help="只运行指定任务；可重复提供",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只验证基线、补丁、测试、快照和 Prompt，不调用 LLM",
    )
    parser.add_argument(
        "--repetitions",
        type=int,
        help="覆盖配置中的每任务调用次数",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    summary = run_baseline(
        task_ids=None if args.all else args.task_ids,
        dry_run=args.dry_run,
        repetitions=args.repetitions,
    )
    expected_status = "dry_run_ok" if args.dry_run else "success"
    if any(task.get("status") != expected_status for task in summary["tasks"]):
        raise SystemExit(1)
