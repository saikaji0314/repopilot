# 工作区路径安全检查
from pathlib import Path

class WorkspacePathGuard:
    def __init__(self, workspace_path: Path):
        self.workspace_path = Path.resolve(workspace_path)

    def check_path(self, relative_path: Path | str) -> None:
        if Path(relative_path).is_absolute():
            raise ValueError("工具路径必须是相对路径")
        candidate_path = Path.resolve(self.workspace_path / relative_path)
        try:
            candidate_path.relative_to(self.workspace_path)
        except ValueError:
            raise ValueError(f"路径 {relative_path} 不在工作区范围内")
