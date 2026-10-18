"""把冻结开发案例接入现有工作流；候选补丁仍需官方评分。"""

import argparse
import json
from pathlib import Path
import tempfile

from repopilot.core.config import PROJECT_ROOT, RuntimeSettings, load_profile, load_settings, load_task
from repopilot.evaluation.swebench import digest, encoded, export_prediction, load_case, write_json
from repopilot.execution.workspace import WorkspaceError, head_commit, run_git, source_snapshot
from repopilot.orchestration import workflow
from repopilot.orchestration.state import attach, canonical_hash
from repopilot.storage.jobs import JobStore, job_lock


def validate_inputs(case_directory, task, settings, runtime_root, output):
    manifest, record = load_case(case_directory)
    repository = task.repository.resolve(strict=True)
    if task.request != record['problem_statement']:
        raise ValueError('benchmark_request_mismatch')
    if head_commit(repository) != record['base_commit']:
        raise ValueError('benchmark_base_commit_mismatch')
    if run_git(repository, 'status', '--porcelain=v1', '--ignored', '--untracked-files=all').stdout.strip():
        raise ValueError('benchmark_source_must_be_clean')
    if task.execution.backend != 'docker' or not task.checks:
        raise ValueError('benchmark_requires_docker_and_public_checks')
    if settings.memory_enabled:
        raise ValueError('benchmark_memory_must_be_disabled')
    for directory in (case_directory, runtime_root, output):
        path = Path(directory).resolve()
        if path.is_relative_to(repository) or repository.is_relative_to(path):
            raise ValueError('benchmark_control_and_repository_must_be_separate')
    return manifest, record['base_commit']


def frozen_diff(repository, base_commit):
    """临时索引包含新增文件；不改候选真实索引，不截断补丁。"""
    with tempfile.TemporaryDirectory(prefix='repopilot-patch-') as temporary:
        environment = {'GIT_INDEX_FILE': str(Path(temporary) / 'index')}
        run_git(repository, 'read-tree', base_commit, extra_env=environment)
        run_git(repository, 'add', '-A', '--', '.', extra_env=environment)
        return run_git(repository, 'diff', '--cached', '--binary', '--no-ext-diff',
                       '--no-textconv', base_commit, extra_env=environment).stdout


def capture_candidate(case_directory, runtime_root, run_id, output, binding):
    """保留失败尝试的部分候选；工作流结论与基准结论始终分开。"""
    store = JobStore(runtime_root)
    with job_lock(store.root, run_id):
        state = store.get(run_id)
        manifest, _ = load_case(case_directory)
        if (canonical_hash(state['task']) != binding['task_sha256'] or
                state['base_commit'] != binding['base_commit'] or state['issue'] is not None or
                canonical_hash(state['settings']) != binding['settings_sha256'] or
                digest(encoded(manifest)) != binding['case_manifest_sha256']):
            raise ValueError('benchmark_run_binding_mismatch')
        if state['status'] in {'ready', 'running', 'paused', 'recovery_required'}:
            raise ValueError('benchmark_run_not_finished')
        workspace = attach(state, store.root)
        if workspace is None or not workspace.path.exists():
            return {'run_id': run_id, 'workflow_status': state['status'],
                    'status': 'no_candidate', 'stop_reason': state.get('stop_reason')}
        before = source_snapshot(workspace.path)
        patch = frozen_diff(workspace.path, binding['base_commit'])
        if source_snapshot(workspace.path) != before:
            raise ValueError('benchmark_candidate_changed_during_export')
        patch_path = output / 'candidate.patch'
        patch_path.write_bytes(patch)
        prediction = export_prediction(case_directory, output / 'prediction', kind='agent',
                                       label=binding['label'], patch=patch_path)
        prediction['run'] = {**binding, 'run_id': run_id, 'candidate_snapshot': before,
                             'state_sha256': digest(encoded(state)), 'workflow_status': state['status'],
                             'stop_reason': state.get('stop_reason'), 'partial': state['status'] != 'awaiting_approval'}
        write_json(output / 'prediction' / 'provenance.json', prediction)
        return {'run_id': run_id, 'workflow_status': state['status'], 'status': 'not_evaluated',
                'stop_reason': state.get('stop_reason'), 'patch_sha256': digest(patch),
                'patch_bytes': len(patch), 'predictions': str(output / 'prediction' / 'predictions.jsonl')}


def solve(case_directory, task, settings, runtime_root, output, *, label, model=None):
    manifest, base_commit = validate_inputs(case_directory, task, settings, runtime_root, output)
    if not label.strip() or '\n' in label or '\r' in label:
        raise ValueError('benchmark_label_required')
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    state = workflow.create_job(task, settings, runtime_root, workspace=True, review=True,
                                require_approval=True, base_commit=base_commit)
    try:
        code_revision = head_commit(PROJECT_ROOT)
        code_dirty = bool(run_git(PROJECT_ROOT, 'status', '--porcelain=v1', '--untracked-files=all').stdout.strip())
    except (WorkspaceError, OSError):
        code_revision, code_dirty = None, None
    binding = {'task_sha256': canonical_hash(state['task']), 'base_commit': state['base_commit'],
               'case_manifest_sha256': digest(encoded(manifest)), 'label': label,
               'model': settings.model, 'settings_sha256': canonical_hash(state['settings']),
               'repopilot_revision': code_revision, 'repopilot_dirty': code_dirty}
    # 模型调用前留下运行身份；崩溃时仍可从现有JobStore定位现场与预算。
    write_json(output / 'attempt.json', {**binding, 'run_id': state['run_id'], 'status': 'started'})
    summary = workflow.resume(state['run_id'], settings, runtime_root, model=model)
    result = capture_candidate(case_directory, runtime_root, state['run_id'], output, binding)
    result.update(cost=summary['cost'], model_calls=summary['model_calls'],
                  tool_calls=summary['tool_calls'], review=summary['review'])
    write_json(output / 'result.json', result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('case', type=Path)
    parser.add_argument('--task', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--label', required=True)
    parser.add_argument('--runtime-root', type=Path, default=PROJECT_ROOT / '.local/runtime')
    parser.add_argument('--profile', type=Path)
    args = parser.parse_args(argv)
    settings = load_settings()
    if args.profile:
        settings = RuntimeSettings.model_validate({**settings.model_dump(), **load_profile(args.profile)})
    result = solve(args.case, load_task(args.task), settings, args.runtime_root, args.output, label=args.label)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
