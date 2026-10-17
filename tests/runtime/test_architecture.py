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
        if fullname.startswith('repopilot.') and not (
            fullname == 'repopilot.runtime' or fullname.startswith('repopilot.runtime.')
        ):
            raise AssertionError('legacy dependency: ' + fullname)

sys.meta_path.insert(0, RejectLegacy())
import repopilot.runtime
for module in pkgutil.walk_packages(repopilot.runtime.__path__, 'repopilot.runtime.'):
    importlib.import_module(module.name)
"""
    result = subprocess.run([sys.executable, '-c', script],
        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
