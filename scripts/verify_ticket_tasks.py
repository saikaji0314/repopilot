"""本地验收新增工单题：干净通过 → 注入失败 → 参考恢复后通过。

不加载密钥、不调用模型；只在自动清理的临时工作区应用/反向应用补丁。
运行：python -m scripts.verify_ticket_tasks
"""

import json
import subprocess
from pathlib import Path

from repopilot.legacy.config import PROJECT_ROOT
from repopilot.legacy.task_loader import parse_task
from repopilot.legacy.verification import run_command, verify_task_defect
from repopilot.legacy.workspace import apply_mutation, prepared_task_workspace


TASK_IDS = ("flaskr-filter-007", "flaskr-cache-008")


def _restore(workspace: Path, patch: Path) -> None:
    for args in (["git", "apply", "--reverse", "--check", str(patch)],
                 ["git", "apply", "--reverse", str(patch)]):
        result = subprocess.run(args, cwd=workspace, capture_output=True,
                                text=True, timeout=30, check=False)
        if result.returncode:
            raise RuntimeError(result.stderr)


def verify_ticket_task(task_id: str) -> dict:
    if task_id not in TASK_IDS:
        raise ValueError("只支持本次新增的两道工单题")
    task_dir = PROJECT_ROOT / "evals" / "tasks" / task_id
    task = parse_task(task_dir / "task.yaml")
    clean_repo = PROJECT_ROOT / task["repository"]["baseline_path"]
    patch = task_dir / "mutation.patch"
    full_command = "python -m pytest -q -p no:cacheprovider"
    with prepared_task_workspace(clean_repo, task_dir, task) as workspace:
        # prepared_task_workspace 默认已注入；先反向应用得到带新功能的干净版。
        _restore(workspace, patch)
        clean = run_command(full_command, workspace, timeout_seconds=120)
        if clean.returncode != 0 or "31 passed" not in clean.stdout:
            raise AssertionError(f"扩展干净版失败：{clean.stdout}\n{clean.stderr}")

        apply_mutation(workspace, patch)
        defect = verify_task_defect(task, workspace)
        if defect.returncode != 1:
            raise AssertionError("必须是测试断言失败，不能以导入/收集错误代替缺陷")
        # 评估资料不应被复制进 Agent 可见工作区。
        assert not list(workspace.rglob("oracle.yaml"))
        assert not list(workspace.rglob("mutation.patch"))

        _restore(workspace, patch)
        repaired = run_command(full_command, workspace, timeout_seconds=120)
        if repaired.returncode != 0 or "31 passed" not in repaired.stdout:
            raise AssertionError(f"参考恢复失败：{repaired.stdout}\n{repaired.stderr}")
        return {
            "task_id": task_id,
            "clean": clean.to_dict(),
            "defective": defect.to_dict(),
            "repaired": repaired.to_dict(),
            "reference_repair": "reverse mutation.patch in temporary workspace only",
        }


def main() -> None:
    # 输出而不覆盖任何已有报告；本地重复运行不产生模型费用。
    print(json.dumps([verify_ticket_task(task_id) for task_id in TASK_IDS],
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
