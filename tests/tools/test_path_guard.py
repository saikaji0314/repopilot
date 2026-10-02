"""步骤 2：工作区路径守卫的测试。"""

from pathlib import Path

import pytest

from repopilot.tools.path_guard import WorkspacePathGuard


def test_relative_path_inside_workspace_is_allowed(tmp_path):
    """测试普通相对路径可以留在工作区内。"""
    guard = WorkspacePathGuard(tmp_path)

    guard.check_path(Path("flaskr/blog.py"))


def test_parent_path_cannot_escape_workspace(tmp_path):
    """测试 ../ 不能读取工作区外文件。"""
    guard = WorkspacePathGuard(tmp_path)

    with pytest.raises(ValueError):
        guard.check_path(Path("../oracle.yaml"))


def test_absolute_path_is_rejected_even_when_it_points_inside_workspace(tmp_path):
    """测试工具接口只接受相对路径，绝对路径一律拒绝。"""
    guard = WorkspacePathGuard(tmp_path)

    with pytest.raises(ValueError):
        guard.check_path(tmp_path / "flaskr" / "blog.py")

