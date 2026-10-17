"""受信开发夹具的补丁评分；工作区外验收不是 OS 安全沙箱。"""

from hashlib import sha256
from contextlib import contextmanager
import json
from pathlib import Path
import platform
import shutil
import tempfile

from repopilot.runtime.repository import run_check
from repopilot.runtime.config import Check
from repopilot.runtime.workspace import WorkspaceError, run_git
from .cases import load_case, tree_digest

MAX_PATCH_BYTES = 300_000


class InvalidPatch(ValueError):
    pass


def git(workspace, *args):
    return run_git(workspace, '-c', 'core.autocrlf=false', '-c', 'core.hooksPath=', *args)


def apply_candidate(workspace, patch, editable):
    stats = git(workspace, 'apply', '--numstat', '-z', str(patch)).stdout.decode('utf-8')
    for entry in stats.rstrip('\0').split('\0'):
        fields = entry.split('\t', 2)
        if len(fields) != 3 or not fields[0].isdigit() or not fields[1].isdigit() or fields[2] not in editable:
            raise InvalidPatch('patch_outside_editable_text_files')
    git(workspace, 'apply', '--check', '--index', str(patch))
    git(workspace, 'apply', '--index', str(patch))
    tree_digest(workspace)


def execute_checks(task):
    results = []
    for name in task.checks:
        result = run_check(task, name, task.checks[name].timeout_seconds)
        result['verdict'] = check_verdict(result)
        results.append(result)
    return results


def check_verdict(result):
    if result['timed_out']:
        return 'timeout'
    try:
        verdict = json.loads(result['output'].splitlines()[-1])['repair_check']
    except (ValueError, IndexError, KeyError, TypeError):
        return 'error'
    expected = {'passed': 0, 'failed': 1}
    return verdict if isinstance(verdict, str) and verdict in expected and result['exit_code'] == expected[verdict] else 'error'


def candidate_status(checks):
    if any(item['verdict'] == 'timeout' for item in checks):
        return 'timeout'
    if any(item['verdict'] == 'error' for item in checks):
        return 'evaluation_error'
    return 'solved' if all(item['verdict'] == 'passed' for item in checks) else 'unsolved'


def valid_baseline(checks):
    return all(item['verdict'] == ('passed' if item['name'].startswith('regression:') else 'failed')
               for item in checks)


def evaluate_workspace(case, directory, workspace, patch, report):
    task = case.scoring_task(directory, workspace)
    report['environment'] = environment_info(task)
    before = tree_digest(workspace)
    report['baseline_checks'] = execute_checks(task)
    if not valid_baseline(report['baseline_checks']) or tree_digest(workspace) != before:
        report.update(status='environment_error', reason='baseline_preflight_failed')
        return
    if patch is not None:
        try:
            apply_candidate(workspace, patch, case.editable)
        except (WorkspaceError, InvalidPatch, ValueError, UnicodeError):
            report.update(status='invalid_patch', reason='patch_rejected')
            return
    before = tree_digest(workspace)
    report['candidate_checks'] = execute_checks(task)
    if tree_digest(workspace) != before:
        report.update(status='invalid_patch', reason='checks_modified_candidate')
        return
    report['status'] = candidate_status(report['candidate_checks'])


def environment_info(task):
    versions = {}
    for runtime in sorted({check.argv[0] for check in task.checks.values()}):
        probe = task.model_copy(update={'checks': {'version': Check(argv=[runtime, '--version'], timeout_seconds=5)}})
        result = run_check(probe, 'version', 5)
        if result['timed_out'] or result['exit_code'] != 0:
            raise ValueError('runtime_version_probe_failed')
        versions[runtime] = result['output'].strip()
    return {'platform': platform.platform(), 'runtimes': versions,
            'git': git(task.repository, '--version').stdout.decode('utf-8').strip()}


def initial_report(case, directory, patch_data):
    return {'schema_version': '1.0', 'case_id': case.id, 'status': 'environment_error',
              'baseline_sha256': case.baseline_sha256,
              'case_sha256': sha256((directory / 'case.json').read_bytes()).hexdigest(),
              'oracle_sha256': tree_digest(directory / 'oracle'),
              'patch_sha256': None if patch_data is None or len(patch_data) > MAX_PATCH_BYTES
                              else sha256(patch_data).hexdigest(),
              'baseline_checks': [], 'candidate_checks': []}


@contextmanager
def prepared_candidate(directory, patch_data, work_root):
    root = Path(work_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='repair-', dir=root) as temporary:
        # TemporaryDirectory只清理此已核对的专用子目录，不触碰原仓库或共享账本。
        temporary = Path(temporary).resolve()
        if not temporary.is_relative_to(root):
            raise ValueError('evaluation_workspace_outside_root')
        workspace = temporary / 'candidate'
        shutil.copytree(directory / 'baseline', workspace)
        frozen_patch = None
        if patch_data is not None:
            frozen_patch = temporary / 'candidate.patch'
            frozen_patch.write_bytes(patch_data)
        git(workspace, 'init', '--quiet')
        git(workspace, 'add', '--all')
        yield workspace, frozen_patch


def evaluate(case_id, patch_path, work_root):
    case, directory = load_case(case_id)
    patch_data = None
    if patch_path is not None:
        with Path(patch_path).open('rb') as stream:
            patch_data = stream.read(MAX_PATCH_BYTES + 1)
    report = initial_report(case, directory, patch_data)
    if patch_data is not None and len(patch_data) > MAX_PATCH_BYTES:
        return report | {'status': 'invalid_patch', 'reason': 'patch_too_large'}
    if tree_digest(directory / 'baseline') != case.baseline_sha256:
        return report | {'reason': 'baseline_hash_mismatch'}
    try:
        with prepared_candidate(directory, patch_data, work_root) as (workspace, frozen_patch):
            evaluate_workspace(case, directory, workspace, frozen_patch, report)
    except (OSError, WorkspaceError, ValueError) as exc:
        report.update(status='environment_error', reason=type(exc).__name__)
    return report
