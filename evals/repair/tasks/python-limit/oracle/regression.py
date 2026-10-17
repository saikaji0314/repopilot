"""固定的原有行为检查，不使用候选可改写的测试文件。"""
import json
from pathlib import Path
import runpy

newest = runpy.run_path(str(Path.cwd() / 'batches.py'))['newest']
try:
    assert newest(['new', 'old']) == ['new', 'old']
    assert newest([]) == []
except AssertionError:
    print(json.dumps({'repair_check': 'failed'}))
    raise SystemExit(1)
print(json.dumps({'repair_check': 'passed'}))
