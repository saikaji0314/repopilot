"""新增题集的本地防退化测试，不调用真实模型。"""

import pytest
import json

from repopilot.config import PROJECT_ROOT
from repopilot.retrieval.index import build_index
from repopilot.task_loader import parse_task
from repopilot.tools.base import SearchSymbolsTool, ToolRegistry
from repopilot.workspace import prepared_task_workspace
import json

from repopilot.config import PROJECT_ROOT
from repopilot.retrieval.index import build_index
from repopilot.task_loader import parse_task
from repopilot.tools.base import SearchSymbolsTool, ToolRegistry
from repopilot.workspace import prepared_task_workspace

from scripts.verify_ticket_tasks import TASK_IDS, verify_ticket_task


@pytest.mark.parametrize("task_id", TASK_IDS)
def test_ticket_task_clean_defect_and_reference_repair(task_id):
    """测完整准备链路，不能把坏环境或本来失败的夹具当有效题目。"""
    report = verify_ticket_task(task_id)
    assert report["clean"]["returncode"] == 0
    assert report["defective"]["returncode"] == 1
    assert report["repaired"]["returncode"] == 0


@pytest.mark.parametrize(("task_id", "query", "defect"), [
    ("flaskr-filter-007", "list_tickets", "repository.find_tickets()"),
    ("flaskr-cache-008", "rename_ticket", 'cache.invalidate(f"tickets:{ticket_id}")'),
])
def test_ticket_defect_is_visible_through_real_symbol_tool(task_id, query, defect):
    """测新增文件在注入后建索引，真实工具读到的是缺陷版而非参考版。"""
    task_dir = PROJECT_ROOT / "evals" / "tasks" / task_id
    task = parse_task(task_dir / "task.yaml")
    with prepared_task_workspace(PROJECT_ROOT / task["repository"]["baseline_path"],
                                 task_dir, task) as workspace:
        registry = ToolRegistry(workspace, [SearchSymbolsTool(build_index(workspace))])
        result = registry.execute("search_symbols", json.dumps({"query": query, "limit": 3}))
        assert result.ok is True
        assert "flaskr/tickets/service.py" in result.data["context"]
        assert defect in result.data["context"]


@pytest.mark.parametrize(("task_id", "query", "defect"), [
    ("flaskr-filter-007", "list_tickets", "repository.find_tickets()"),
    ("flaskr-cache-008", "rename_ticket", 'cache.invalidate(f"tickets:{ticket_id}")'),
])
def test_ticket_defect_is_visible_through_real_symbol_tool(task_id, query, defect):
    """测新增文件在注入后建索引，真实工具读到的是缺陷版而非参考版。"""
    task_dir = PROJECT_ROOT / "evals" / "tasks" / task_id
    task = parse_task(task_dir / "task.yaml")
    with prepared_task_workspace(PROJECT_ROOT / task["repository"]["baseline_path"],
                                 task_dir, task) as workspace:
        registry = ToolRegistry(workspace, [SearchSymbolsTool(build_index(workspace))])
        result = registry.execute("search_symbols", json.dumps({"query": query, "limit": 3}))
        assert result.ok is True
        assert "flaskr/tickets/service.py" in result.data["context"]
        assert defect in result.data["context"]
