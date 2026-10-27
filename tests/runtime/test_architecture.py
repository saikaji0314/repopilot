"""禁止新版悄悄依赖旧版实现，避免后续清理破坏运行时边界。"""

from pathlib import Path
import subprocess
import sys


def test_runtime_imports_without_legacy_modules():
    script = """
import importlib
from importlib.abc import MetaPathFinder
import pkgutil
import sys

class RejectLegacy(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'repopilot.legacy' or fullname.startswith('repopilot.legacy.'):
            raise AssertionError('legacy dependency: ' + fullname)

sys.meta_path.insert(0, RejectLegacy())
import repopilot
for name in ('agents', 'collaboration', 'core', 'delivery', 'execution', 'memory', 'orchestration', 'presentation', 'storage'):
    package = importlib.import_module('repopilot.' + name)
    for module in pkgutil.walk_packages(package.__path__, package.__name__ + '.'):
        importlib.import_module(module.name)
import repopilot.cli
import repopilot.runtime
"""
    result = subprocess.run([sys.executable, '-c', script],
        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_compatibility_imports_share_module_identity():
    from repopilot.runtime import workflow, sandbox
    import repopilot.orchestration.workflow as actual_workflow
    import repopilot.execution.sandbox as actual_sandbox
    assert workflow is actual_workflow and sandbox is actual_sandbox
