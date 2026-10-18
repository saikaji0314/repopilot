"""真实Git与确定性模型验证实验绑定；不把模拟容器等同于OS隔离。"""

from contextlib import contextmanager
import json

import pytest

from repopilot.core.config import Check, ExecutionConfig, RuntimeSettings, Task
from repopilot.evaluation import solver, swebench
from repopilot.execution.files import Repository
from repopilot.execution.repository import run_check
from repopilot.execution.workspace import head_commit, run_git
from repopilot.orchestration import phases
from tests.runtime.test_review_loop import FakeModel, implement_done, make_repo, review_approved, turn


@pytest.fixture
def inputs(tmp_path):
    repo = make_repo(tmp_path)
    request = 'Set value to 2.'
    record = {'instance_id': 'example__repo-1', 'repo': 'example/repo', 'version': '1',
              'base_commit': head_commit(repo), 'problem_statement': request,
              'patch': 'not provided to the model', 'test_patch': 'secret acceptance'}
    dataset = tmp_path / 'data.jsonl'
    dataset.write_text(json.dumps(record), encoding='utf-8')
    case = tmp_path / 'case'
    swebench.prepare(dataset, record['instance_id'], case, dataset_name='fixture',
                     dataset_revision='a' * 40, harness_revision='b' * 40)
    task = Task(id='benchmark', request=request, repository=repo,
                checks={'unit': Check(argv=['{python}', '-c', 'print("checked")'])},
                execution=ExecutionConfig(backend='docker', image='sha256:' + 'c' * 64))
    settings = RuntimeSettings(api_key='synthetic-private-key', max_model_calls=10)
    return case, task, settings, tmp_path / 'runtime', tmp_path / 'attempt'


@pytest.mark.parametrize('change,reason', [
    ('request', 'request_mismatch'), ('base', 'base_commit_mismatch'),
    ('dirty', 'source_must_be_clean'), ('memory', 'memory_must_be_disabled'),
    ('local', 'requires_docker'), ('overlap', 'must_be_separate')])
def test_invalid_experiment_rejected_before_any_model_call(inputs, change, reason):
    case, task, settings, runtime, output = inputs
    if change == 'request':
        task = task.model_copy(update={'request': 'different request'})
    elif change == 'base':
        run_git(task.repository, '-c', 'user.name=test', '-c', 'user.email=test@example.test',
                'commit', '--allow-empty', '-m', 'new head')
    elif change == 'dirty':
        (task.repository / 'untracked.txt').write_text('unexpected input')
    elif change == 'memory':
        settings = settings.model_copy(update={'memory_enabled': True})
    elif change == 'local':
        task = task.model_copy(update={'execution': ExecutionConfig()})
    else:
        output = task.repository / 'output'
    with pytest.raises(ValueError, match=reason):
        solver.solve(case, task, settings, runtime, output, label='fixture', model=object())
    assert not runtime.exists() and not output.exists()


def test_full_patch_includes_new_files_without_changing_real_index(inputs):
    _, task, _, _, _ = inputs
    repo = task.repository
    before = run_git(repo, 'diff', '--cached', '--binary').stdout
    (repo / 'app.py').write_text('value = 2\n')
    (repo / 'new.py').write_text('created = True\n')
    patch = solver.frozen_diff(repo, head_commit(repo))
    assert b'+value = 2' in patch and b'diff --git a/new.py b/new.py' in patch
    assert run_git(repo, 'diff', '--cached', '--binary').stdout == before


@contextmanager
def simulated_docker(task, root, **kwargs):
    class Session(Repository):
        def run_check(self, name, timeout):
            local = task.model_copy(update={'execution': ExecutionConfig()})
            return run_check(local, name, timeout, root=self.root)
    yield Session(root)


def test_workflow_and_candidate_are_bound_but_not_called_solved(inputs, monkeypatch):
    case, task, settings, runtime, output = inputs
    monkeypatch.setattr(phases, 'repository_session', simulated_docker)
    model = FakeModel(responses=[turn('write_file', {'path': 'app.py', 'content': 'value = 2\n'}),
                                 implement_done(), review_approved()])
    result = solver.solve(case, task, settings, runtime, output, label='fixture', model=model)
    assert result['status'] == 'not_evaluated' and result['workflow_status'] == 'awaiting_approval'
    assert b'+value = 2' in (output / 'candidate.patch').read_bytes()
    provenance = json.loads((output / 'prediction/provenance.json').read_text())
    assert provenance['run']['run_id'] == result['run_id']
    assert provenance['run']['base_commit'] == head_commit(task.repository)
    assert provenance['run']['task_sha256'] and provenance['run']['settings_sha256']
    assert provenance['kind'] == 'agent' and not provenance['run']['partial']
    assert 'synthetic-private-key' not in (output / 'attempt.json').read_text()
    trace = (runtime / 'runs' / result['run_id'] / 'events.jsonl').read_text(encoding='utf-8')
    assert 'not provided to the model' not in trace and 'secret acceptance' not in trace
    assert (task.repository / 'app.py').read_text() == 'value = 1\n'


def test_failed_run_keeps_partial_patch_and_does_not_claim_success(inputs, monkeypatch):
    case, task, settings, runtime, output = inputs
    monkeypatch.setattr(phases, 'repository_session', simulated_docker)
    settings = settings.model_copy(update={'max_model_calls': 1})
    model = FakeModel(responses=[turn('write_file', {'path': 'app.py', 'content': 'value = 2\n'})])
    result = solver.solve(case, task, settings, runtime, output, label='limited', model=model)
    assert result['workflow_status'] == 'limit_reached' and result['status'] == 'not_evaluated'
    assert b'+value = 2' in (output / 'candidate.patch').read_bytes()
    provenance = json.loads((output / 'prediction/provenance.json').read_text())
    assert provenance['run']['partial'] is True
