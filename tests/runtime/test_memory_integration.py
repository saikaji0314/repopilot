"""记忆实际接入阶段载荷与恢复；用确定性模型验证，不等同模型效果实验。"""

import json

import repopilot.agents.loop as engine
import repopilot.cli as cli
import repopilot.orchestration.workflow as workflow
from repopilot.memory.store import MemoryStore
from repopilot.storage.jobs import JobStore
from tests.runtime.test_memory import add_file
from tests.runtime.test_review_loop import setup, FakeModel, turn, implement_done, review_approved, review_changes


def responses():
    return [turn('write_file', {'path': 'app.py', 'content': 'value = 2\n'}), implement_done(), review_approved()]


def test_frozen_memory_reaches_both_roles_but_changed_source_is_filtered(tmp_path, monkeypatch):
    task, settings, root = setup(tmp_path, memory_enabled=True)
    add_file(task.repository, 'README.md', 'app documentation\napp second reference\n')
    store = MemoryStore(root)
    source = store.remember(task.repository, 'app.py', 1, 1)
    document = store.remember(task.repository, 'README.md', 1, 1)
    first = workflow.start(task, settings, root, model=FakeModel(responses=[]), stop_after='prepare')
    later = store.remember(task.repository, 'README.md', 2, 2)
    seen, original = {}, engine.run_phase
    def capture(**kwargs):
        seen[kwargs['phase']] = kwargs['payload']
        return original(**kwargs)
    monkeypatch.setattr(engine, 'run_phase', capture)
    result = workflow.resume(first['run_id'], settings, root, model=FakeModel(responses=responses()))
    assert result['status'] == 'awaiting_approval', result
    initial_ids = {entry['id'] for entry in seen['implement']['repository_memory']['entries']}
    review_ids = {entry['id'] for entry in seen['review']['repository_memory']['entries']}
    assert initial_ids == {source['id'], document['id']}
    assert review_ids == {document['id']}
    assert later['id'] not in initial_ids
    assert any(item['reason'] == 'source_changed' for item in seen['review']['repository_memory']['omitted'])
    state = JobStore(root).get(first['run_id'])
    snapshot_id = state['memory_snapshot']['snapshot_id']
    again = workflow.resume(first['run_id'], settings, root, model=FakeModel(responses=[]))
    assert again['model_calls'] == result['model_calls']
    assert JobStore(root).get(first['run_id'])['memory_snapshot']['snapshot_id'] == snapshot_id
    candidates = [record for record in store.records(task.repository) if record['status'] == 'candidate']
    assert candidates and candidates[0]['source_run'] == first['run_id']
    assert candidates[0]['content'] == 'value = 2'
    assert candidates[0]['id'] not in {entry['id'] for entry in store.search(task.repository, 'app')['entries']}


def test_disabled_memory_does_not_create_store(tmp_path):
    task, settings, root = setup(tmp_path)
    result = workflow.start(task, settings, root, model=FakeModel(responses=responses()))
    assert result['status'] == 'awaiting_approval'
    assert not (root / 'memory.sqlite3').exists()


def test_bad_optional_memory_database_does_not_break_delivery(tmp_path):
    task, settings, root = setup(tmp_path, memory_enabled=True)
    root.mkdir()
    (root / 'memory.sqlite3').write_bytes(b'invalid sqlite')
    result = workflow.start(task, settings, root, model=FakeModel(responses=responses()))
    assert result['status'] == 'awaiting_approval', result
    events = (root / 'runs' / result['run_id'] / 'events.jsonl').read_text(encoding='utf-8')
    assert 'memory_warning' in events


def test_memory_cli_needs_no_credentials(tmp_path, monkeypatch, capsys):
    from tests.runtime.test_review_loop import make_repo
    repo = make_repo(tmp_path)
    def unavailable(*args, **kwargs):
        raise AssertionError('不应加载凭证')
    monkeypatch.setattr(cli, 'load_settings', unavailable)
    monkeypatch.setattr(cli, 'open_forge', unavailable)
    base = ['--repository', str(repo), '--output', str(tmp_path / 'runtime')]
    assert cli.main(['memory', 'remember', *base, '--path', 'app.py', '--end-line', '1']) == 0
    saved = json.loads(capsys.readouterr().out)['memory']
    assert cli.main(['memory', 'search', *base, '--query', 'app']) == 0
    assert json.loads(capsys.readouterr().out)['memory']['entries'][0]['id'] == saved['id']
    assert cli.main(['memory', 'forget', *base, '--id', saved['id'], '--reason', '清理']) == 0
    capsys.readouterr()
    assert cli.main(['memory', 'search', *base, '--query', 'app']) == 0
    assert not json.loads(capsys.readouterr().out)['memory']['entries']


def test_cli_memory_switch_overrides_profile(tmp_path, monkeypatch, capsys):
    task, settings, root = setup(tmp_path)
    monkeypatch.setattr(cli, 'load_settings', lambda: settings)
    monkeypatch.setattr(cli, 'load_task', lambda path: task)
    received = []
    def start(task, configured, *args, **kwargs):
        received.append(configured.memory_enabled)
        return {'status': 'completed'}
    monkeypatch.setattr(cli.workflow, 'start', start)
    profile = tmp_path / 'profile.yaml'
    profile.write_text('memory_enabled: true\n')
    assert cli.main(['run', 'task.yaml', '--profile', str(profile), '--no-memory']) == 0
    capsys.readouterr()
    assert cli.main(['run', 'task.yaml', '--memory']) == 0
    assert received == [False, True]


def test_rework_does_not_reuse_changed_file_facts(tmp_path, monkeypatch):
    task, settings, root = setup(tmp_path, memory_enabled=True)
    store = MemoryStore(root)
    record = store.remember(task.repository, 'app.py', 1, 1)
    seen, original = {}, engine.run_phase
    def capture(**kwargs):
        seen[kwargs['phase']] = kwargs['payload']
        return original(**kwargs)
    monkeypatch.setattr(engine, 'run_phase', capture)
    model = FakeModel(responses=[*responses()[:2], review_changes(),
        turn('write_file', {'path': 'app.py', 'content': 'value = 3\n'}), implement_done(), review_approved()])
    result = workflow.start(task, settings, root, model=model)
    assert result['status'] == 'awaiting_approval'
    assert seen['implement']['repository_memory']['entries'][0]['id'] == record['id']
    assert not seen['rework']['repository_memory']['entries']
    assert seen['rework']['repository_memory']['omitted'][0]['reason'] == 'source_changed'
