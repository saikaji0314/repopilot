"""较大仓库任务防退化测试；安装专用测试环境后本地运行。"""
import os
import pytest

from repopilot.config import PROJECT_ROOT
from scripts.verify_flask_source_tasks import TASK_COUNTS, verify_source_task


@pytest.mark.parametrize("task_id", TASK_COUNTS)
def test_source_task_is_reproducible(task_id):
    python = PROJECT_ROOT / ".venv-flask-source" / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file() or not (PROJECT_ROOT / "target-repos/flask-3.1.3").is_dir():
        pytest.skip("先按 LARGER_REPO.md 准备源码与独立测试环境")
    report = verify_source_task(task_id)
    assert report["clean"]["returncode"] == report["repaired"]["returncode"] == 0
    assert report["defective"]["returncode"] == 1
