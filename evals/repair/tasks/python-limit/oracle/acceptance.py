"""候选工作区之外的独立边界验收；这是公开开发题，不是留出题。"""
import json
from pathlib import Path
import runpy

newest = runpy.run_path(str(Path.cwd() / 'batches.py'))['newest']
try:
    rows = ['newest', 'middle', 'oldest']
    assert newest(rows, 1) == ['newest']
    assert newest(rows, 2) == ['newest', 'middle']
    assert newest(rows, 10) == rows
    assert newest([], 2) == []
    assert rows == ['newest', 'middle', 'oldest']
    for invalid in (0, -1, True, 1.5, '2'):
        try:
            newest(rows, invalid)
        except ValueError:
            continue
        raise AssertionError('invalid limit was accepted')
except AssertionError:
    print(json.dumps({'repair_check': 'failed'}))
    raise SystemExit(1)
print(json.dumps({'repair_check': 'passed'}))
