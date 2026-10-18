"""Flask 源码任务本地预检；不读取密钥、不调用模型。"""
from repopilot.legacy.agent.trace import save_trace
from repopilot.legacy.agent_runner import _run_id
from repopilot.legacy.config import PROJECT_ROOT
from repopilot.legacy.repository_runtime import resolve_repository
from repopilot.legacy.task_loader import parse_task
from repopilot.legacy.tools.testing import run_test
from repopilot.legacy.verification import run_command, verify_task_defect
from repopilot.legacy.workspace import apply_mutation, prepared_task_workspace
from scripts.verify_ticket_tasks import _restore


TASK_COUNTS = {"flask-blueprint-009": 493, "flask-session-path-010": 494}


def verify_source_task(task_id: str) -> dict:
    expected_count = TASK_COUNTS[task_id]
    task_dir = PROJECT_ROOT / "evals" / "tasks" / task_id
    task = parse_task(task_dir / "task.yaml")
    repo, python = resolve_repository(task, PROJECT_ROOT / "target-repos/flaskr-tutorial")
    full = "python -m pytest -q -p no:cacheprovider"
    with prepared_task_workspace(repo, task_dir, task) as workspace:
        patch = task_dir / "mutation.patch"
        _restore(workspace, patch)
        clean = run_command(full, workspace, timeout_seconds=120, python_executable=python)
        assert clean.returncode == 0, clean.stdout + clean.stderr
        assert f"{expected_count} passed" in clean.stdout
        apply_mutation(workspace, patch)
        defect = verify_task_defect(task, workspace, python_executable=python)
        assert defect.returncode == 1, defect.stdout + defect.stderr
        # 验证 Agent 实际工具与预检使用相同解释器和当前 src，而非 site-packages。
        observed = run_test(workspace, "tests/test_task_regression.py", python_executable=python)
        assert observed.ok and observed.data["returncode"] == 1
        assert task["initial_evidence"]["summary"] in observed.data["stdout"]
        _restore(workspace, patch)
        repaired = run_command(full, workspace, timeout_seconds=120, python_executable=python)
        assert repaired.returncode == 0, repaired.stdout + repaired.stderr
        assert f"{expected_count} passed" in repaired.stdout
        return {
            "task_id": task_id, "repository": task["repository"],
            "clean": clean.to_dict(), "defective": defect.to_dict(),
            "tool_observation": observed.model_dump(mode="json"),
            "repaired": repaired.to_dict(), "model_called": False,
        }


def main():
    path = PROJECT_ROOT / "evals/runs/preflight" / _run_id() / "flask-source.json"
    reports = []
    for task_id in TASK_COUNTS:
        reports.append(verify_source_task(task_id))
        save_trace(path, reports)
        print(f"{task_id}: clean / defect / tool / repaired verified", flush=True)
    print(f"REPORT={path}")


if __name__ == "__main__":
    main()
