"""公开开发题的固定基线与可信评分配置；不读取模型凭证。"""

from hashlib import sha256
import json
import os
from pathlib import Path
import re

from pydantic import Field

from repopilot.runtime.config import Check, Contract, PROJECT_ROOT, Task
from repopilot.runtime.repository import linked

CASE_ROOT = PROJECT_ROOT / 'evals' / 'repair' / 'tasks'


def tree_digest(root):
    files = {}
    for directory, names, filenames in os.walk(root):
        names[:] = [name for name in names if name != '.git']
        for name in [*names, *filenames]:
            if linked(Path(directory) / name):
                raise ValueError('linked_evaluation_file')
        for name in filenames:
            path = Path(directory) / name
            files[path.relative_to(root).as_posix()] = sha256(path.read_bytes()).hexdigest()
    return sha256(json.dumps(files, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class RepairCase(Contract):
    id: str = Field(pattern=r'^[a-z0-9][a-z0-9-]{0,79}$')
    request: str = Field(min_length=1)
    baseline_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    editable: list[str] = Field(min_length=1)
    regression: dict[str, Check] = Field(min_length=1)
    acceptance: dict[str, Check] = Field(min_length=1)

    def scoring_task(self, directory, workspace):
        checks = {}
        for group, definitions in [('regression', self.regression), ('acceptance', self.acceptance)]:
            for name, check in definitions.items():
                argv = [value.replace('{oracle}', str(directory / 'oracle')) for value in check.argv]
                checks[f'{group}:{name}'] = check.model_copy(update={'argv': argv})
        return Task(id=self.id, request=self.request, repository=workspace, checks=checks)


def load_case(case_id):
    if not isinstance(case_id, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,79}', case_id):
        raise ValueError('invalid_case_id')
    directory = CASE_ROOT / case_id
    case = RepairCase.model_validate_json((directory / 'case.json').read_text(encoding='utf-8'))
    if case.id != case_id:
        raise ValueError('case_identity_mismatch')
    for path in case.editable:
        if path not in {p.relative_to(directory / 'baseline').as_posix()
                        for p in (directory / 'baseline').rglob('*') if p.is_file()}:
            raise ValueError('editable_file_not_in_baseline')
    return case, directory
