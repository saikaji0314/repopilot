"""归档、边界失败和真实容器集成分别验证，不把假模型当修复效果。"""

import io
import json
import os
from pathlib import Path
import sys
import tarfile

import pytest
from pydantic import ValidationError

from repopilot.core.config import Check, ExecutionConfig
import repopilot.execution.archive as archive
from repopilot.execution.sandbox import repository_session
from repopilot.execution.transport import DockerTransport, SandboxError
import repopilot.execution.repository as repository
import repopilot.orchestration.workflow as workflow
from repopilot.execution.workspace import run_git
from repopilot.storage.budget import RunLimit
from repopilot.storage.jobs import JobStore
from tests.runtime.test_review_loop import setup, FakeModel, turn, implement_done, review_approved

IMAGE = 'python@sha256:' + 'a' * 64


def tar(entries):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w') as stream:
        for name, kind in entries:
            member = tarfile.TarInfo(name)
            member.type = kind
            if kind == tarfile.REGTYPE:
                member.size = 1
                stream.addfile(member, io.BytesIO(b'x'))
            else:
                member.linkname = '../escape'
                stream.addfile(member)
    return output.getvalue()


@pytest.mark.parametrize('image', ['', 'python:latest', 'python@sha256:short'])
def test_docker_requires_immutable_image(image):
    with pytest.raises(ValidationError):
        ExecutionConfig(backend='docker', image=image)


def test_docker_accepts_fixed_local_image_id():
    image = 'sha256:' + 'a' * 64
    assert ExecutionConfig(backend='docker', image=image).image == image


@pytest.mark.parametrize('name', ['../escape', '/absolute', 'a/../escape', 'a\\escape', '.env',
                                  '.git/config', 'a:stream', 'CON.txt', 'a./file'])
def test_malicious_archive_paths_never_import(tmp_path, name):
    (tmp_path / 'keep.txt').write_text('original')
    with pytest.raises(ValueError):
        archive.unpack(tar([('safe.txt', tarfile.REGTYPE), (name, tarfile.REGTYPE)]), 10000)
    assert list(tmp_path.iterdir()) == [tmp_path / 'keep.txt']
    assert (tmp_path / 'keep.txt').read_text() == 'original'


@pytest.mark.parametrize('kind', [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE])
def test_archive_rejects_links_and_special_files(kind):
    with pytest.raises(ValueError, match='special_file'):
        archive.unpack(tar([('unsafe', kind)]), 10000)


def test_archive_rejects_duplicates_case_collisions_and_overflow():
    for entries in [[('a', tarfile.REGTYPE), ('a', tarfile.REGTYPE)],
                    [('a', tarfile.REGTYPE), ('A', tarfile.REGTYPE)],
                    [('dir/a', tarfile.REGTYPE), ('DIR/b', tarfile.REGTYPE)],
                    [('a', tarfile.REGTYPE), ('a/b', tarfile.REGTYPE)]]:
        with pytest.raises(ValueError):
            archive.unpack(tar(entries), 10000)
    with pytest.raises(ValueError, match='size_limit'):
        archive.unpack(tar([('a', tarfile.REGTYPE), ('b', tarfile.REGTYPE)]), 1)


def test_import_checks_readonly_and_all_paths_before_writing(tmp_path):
    (tmp_path / 'file.txt').write_text('before')
    before = archive.inventory(tmp_path, 10000)
    changed = {'file.txt': (b'after', 0o644)}
    with pytest.raises(ValueError, match='readonly_tree_changed'):
        archive.apply(tmp_path, before, changed, writable=False, limit=10000)
    changed['.gitattributes'] = (b'* filter=unexpected', 0o644)
    with pytest.raises(ValueError, match='git_control'):
        archive.apply(tmp_path, before, changed, writable=True, limit=10000)
    assert (tmp_path / 'file.txt').read_text() == 'before'
    with pytest.raises(ValueError, match='git_control'):
        archive.apply(tmp_path, before, {'.GITATTRIBUTES': (b'* filter=unexpected', 0o644)},
                      writable=True, limit=10000)
    (tmp_path / 'file.txt').write_text('external change')
    with pytest.raises(ValueError, match='host_snapshot_changed'):
        archive.apply(tmp_path, before, before, writable=True, limit=10000)


def test_snapshot_roundtrip_binary_delete_and_cache_filtering(tmp_path):
    (tmp_path / 'old.txt').write_text('old')
    before = archive.inventory(tmp_path, 10000)
    after = archive.unpack(archive.pack({'new.bin': (b'\0\xff', 0o644),
                                        '__pycache__/cache.pyc': (b'cache', 0o644)}), 10000)
    archive.apply(tmp_path, before, after, writable=True, limit=10000)
    assert not (tmp_path / 'old.txt').exists()
    assert (tmp_path / 'new.bin').read_bytes() == b'\0\xff'


def test_import_rejects_file_directory_transition_before_other_writes(tmp_path):
    (tmp_path / 'a').write_text('original')
    (tmp_path / 'folder').mkdir()
    (tmp_path / 'folder' / 'file').write_text('nested')
    before = archive.inventory(tmp_path, 10000)
    after = {'a': (b'changed', 0o644), 'folder': (b'file now', 0o644)}
    with pytest.raises(ValueError, match='file_directory_transition'):
        archive.apply(tmp_path, before, after, writable=True, limit=10000)
    assert (tmp_path / 'a').read_text() == 'original'


def transport_program(code):
    transport = object.__new__(DockerTransport)
    transport.prefix = [sys.executable, '-c', code]
    return transport


def test_transport_bounds_output_time_and_parent_environment(monkeypatch):
    monkeypatch.setenv('DS_API_KEY', 'synthetic-do-not-inherit')
    assert transport_program("import os; print('DS_API_KEY' in os.environ)").run('test').strip() == b'False'
    with pytest.raises(SandboxError, match='output_limit'):
        transport_program("print('x'*1000000)").run('test', cap=1000)
    with pytest.raises(SandboxError, match='timeout'):
        transport_program('import time; time.sleep(30)').run('test', timeout=0.1)
    out, code = transport_program("import sys; print('bad',file=sys.stderr); sys.exit(7)").run('test', allow_failure=True)
    assert code == 7 and b'bad' in out


def test_docker_unavailable_does_not_call_model_or_host_checks(tmp_path, monkeypatch):
    task, settings, root = setup(tmp_path)
    task = task.model_copy(update={'execution': ExecutionConfig(backend='docker', image=IMAGE)})
    def unavailable(*args, **kwargs):
        raise SandboxError('synthetic_docker_unavailable')
    monkeypatch.setattr(DockerTransport, '__init__', lambda self, distribution: None)
    monkeypatch.setattr(DockerTransport, 'run', unavailable)
    monkeypatch.setattr(repository, 'run_check', lambda *a, **k: pytest.fail('host fallback'))
    result = workflow.start(task, settings, root, model=FakeModel(responses=[implement_done()]))
    assert result['status'] == 'workspace_error' and result['model_calls'] == 0
    with pytest.raises(ValueError, match='isolated_execution_required'):
        repository.build_tools(task, lambda: 10)


def docker_config():
    image = os.environ.get('REPOPILOT_TEST_DOCKER_IMAGE')
    if not image:
        pytest.skip('真实 Docker 测试需显式设置已预装镜像摘要')
    return ExecutionConfig(backend='docker', image=image,
                           wsl_distribution=os.environ.get('REPOPILOT_TEST_WSL'))


def test_real_docker_scoped_search_matches_local_and_keeps_guards(tmp_path):
    from repopilot.execution.files import Repository

    task, _, _ = setup(tmp_path)
    (task.repository / 'src' / 'nested').mkdir(parents=True)
    (task.repository / 'docs').mkdir()
    (task.repository / 'src' / 'main.ts').write_text('needle\n', encoding='utf-8')
    (task.repository / 'src' / 'nested' / 'other.ts').write_text('needle\n', encoding='utf-8')
    (task.repository / 'src' / 'upper.TS').write_text('needle\n', encoding='utf-8')
    (task.repository / 'docs' / 'notes.md').write_text('needle\n' * 25, encoding='utf-8')
    task = task.model_copy(update={'execution': docker_config()})
    host = Repository(task.repository)
    with repository_session(task, task.repository, writable=False, deadline=lambda: 90) as session:
        for path, pattern in [('.', '*'), ('src', '*.ts'), ('src/main.ts', '*')]:
            assert session.search('needle', path, pattern) == host.search('needle', path, pattern)
            assert session.listing(path, pattern) == host.listing(path, pattern)
        tools = {t.name: t for t in repository.build_tools(task, lambda: 60,
                 repository=session, checker=session.run_check)}
        assert tools['search_text'].invoke({'query': 'needle', 'path': 'src', 'file_glob': '*.ts'})['scanned_files'] == 2
        assert tools['list_files'].invoke({'path': 'src', 'file_glob': '*.ts'})['files'] == [
            'src/main.ts', 'src/nested/other.ts']
        for path in ['../outside', '.env', '.local', '/etc', 'C:\\outside']:
            with pytest.raises(ValueError, match='path_denied'):
                session.search('needle', path)
        with pytest.raises(ValueError, match='invalid_file_glob'):
            session.search('needle', file_glob='**/*.ts')
    assert (task.repository / 'src' / 'main.ts').read_text(encoding='utf-8') == 'needle\n'


@pytest.mark.parametrize('language', ['python', 'javascript'])
def test_real_docker_tools_verify_and_checkpoint_resume(tmp_path, language, monkeypatch):
    monkeypatch.setenv('DS_API_KEY', 'synthetic-host-key')
    if language == 'javascript':
        image = os.environ.get('REPOPILOT_TEST_NODE_IMAGE')
        if not image:
            pytest.skip('Node 集成测试需显式准备含 Python3 和 Node 的固定镜像')
        monkeypatch.setenv('REPOPILOT_TEST_DOCKER_IMAGE', image)
    config = docker_config()
    task, settings, root = setup(tmp_path, run_timeout_seconds=240)
    filename, content = 'app.py', 'value = 2\n'
    task = task.model_copy(update={'execution': config, 'checks': {'unit': Check(argv=['{python}', '-c',
        "import os,pathlib; from app import value; assert value==2; assert os.getuid()==65532; "
        "assert not pathlib.Path('/mnt/c').exists(); assert not pathlib.Path('.git').exists(); "
        "assert 'DS_API_KEY' not in os.environ; print('isolated-check')"])}})
    if language == 'javascript':
        filename, content = 'normalize.mjs', "export const normalize = s => s.trim().toLowerCase();\n"
        (task.repository / filename).write_text('export const normalize = s => s;\n', encoding='utf-8')
        run_git(task.repository, 'add', filename)
        run_git(task.repository, 'commit', '-m', 'fixture: JavaScript baseline')
        task = task.model_copy(update={'request': '修复 normalize 的首尾空白与大小写处理',
            'checks': {'unit': Check(argv=['node', '--input-type=module', '-e',
            "import fs from 'node:fs'; import assert from 'node:assert/strict'; "
            "import {normalize} from './normalize.mjs'; assert.equal(normalize(' Repo '),'repo'); "
            "assert.equal(process.getuid(),65532); assert.equal(process.env.DS_API_KEY,undefined); "
            "assert.equal(fs.existsSync('.git'),false); console.log('isolated-check');"])}})
    implementation = implement_done()
    implementation.tool_calls[0]['args']['evidence'][0]['path'] = filename
    model = FakeModel(responses=[turn('read_file', {'path': filename}),
        turn('write_file', {'path': filename, 'content': content}),
        turn('run_registered_check', {'name': 'unit'}), implementation,
        turn('read_file', {'path': filename}), review_approved()])
    first = workflow.start(task, settings, root, model=model, stop_after='implement')
    assert first['status'] == 'paused', first
    result = workflow.resume(first['run_id'], settings, root, model=model)
    assert result['status'] == 'awaiting_approval', result
    directory = root / 'runs' / result['run_id']
    checks = json.loads((directory / 'verification.json').read_text(encoding='utf-8'))
    assert checks[0]['exit_code'] == 0 and 'isolated-check' in checks[0]['output_tail']
    events = [json.loads(x) for x in (directory / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
    assert len([x for x in events if x['event'] == 'sandbox_ready']) == 3
    assert all(x['image'] == config.image for x in events if x['event'] == 'sandbox_ready')
    assert all(x['complete'] for x in events if x['event'] == 'sandbox_cleanup')
    assert (task.repository / 'app.py').read_text(encoding='utf-8') == 'value = 1\n'
    if language == 'javascript':
        assert (task.repository / filename).read_text(encoding='utf-8') == 'export const normalize = s => s;\n'


def test_real_docker_limits_and_rejected_artifact(tmp_path):
    config = docker_config()
    task, _, _ = setup(tmp_path)
    task = task.model_copy(update={'execution': config, 'checks': {
        'noise': Check(argv=['{python}', '-c', "print('x'*2000000)"]),
        'sleep': Check(argv=['{python}', '-c', 'import time; time.sleep(60)'], timeout_seconds=0.3),
        'link': Check(argv=['{python}', '-c', "import os; os.symlink('/etc/passwd','unsafe-link')"]),
    }})
    before = (task.repository / 'app.py').read_bytes()
    with pytest.raises(ValueError, match='archive_special_file'):
        with repository_session(task, task.repository, writable=True, deadline=lambda: 90) as session:
            assert session.run_check('noise', 10)['output_limited']
            assert session.run_check('sleep', 1)['timed_out']
            assert session.run_check('link', 10)['exit_code'] == 0
    assert (task.repository / 'app.py').read_bytes() == before
    assert not (task.repository / 'unsafe-link').exists()


def test_real_docker_readonly_check_mutation_is_rejected(tmp_path):
    task, _, _ = setup(tmp_path)
    task = task.model_copy(update={'execution': docker_config(), 'checks': {'mutate': Check(
        argv=['{python}', '-c', "open('app.py','w').write('value = 99\\n')"])}})
    before = (task.repository / 'app.py').read_bytes()
    with pytest.raises(ValueError, match='readonly_tree_changed'):
        with repository_session(task, task.repository, writable=False, deadline=lambda: 90) as session:
            with pytest.raises(ValueError, match='readonly_session'):
                session.write('app.py', 'value = 100\n')
            assert session.run_check('mutate', 10)['exit_code'] == 0
    assert (task.repository / 'app.py').read_bytes() == before


def test_real_docker_controlled_limit_preserves_validated_partial_changes(tmp_path):
    task, settings, root = setup(tmp_path, max_model_calls=1, run_timeout_seconds=120)
    task = task.model_copy(update={'execution': docker_config()})
    result = workflow.start(task, settings, root, model=FakeModel(responses=[
        turn('write_file', {'path': 'app.py', 'content': 'value = 2\n'})]))
    assert result['status'] == 'limit_reached' and result['stop_reason'] == 'max_model_calls'
    state = JobStore(root).get(result['run_id'])
    candidate = workflow.attach(state, root).path
    assert (candidate / 'app.py').read_text() == 'value = 2\n'
    assert (task.repository / 'app.py').read_text() == 'value = 1\n'
    assert state['in_flight'] == 'implement'  # 恢复仍需显式核对，没有自动继续或批准。
    events = [json.loads(line) for line in
              (root / 'runs' / result['run_id'] / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
    assert any(item['event'] == 'sandbox_partial_checkpoint' for item in events)


def test_real_docker_controlled_limit_still_rejects_unsafe_partial_artifacts(tmp_path):
    task, _, _ = setup(tmp_path)
    task = task.model_copy(update={'execution': docker_config(), 'checks': {'link': Check(
        argv=['{python}', '-c', "import os; os.symlink('/etc/passwd','unsafe-link')"])}})
    before = (task.repository / 'app.py').read_bytes()
    with pytest.raises(ValueError, match='archive_special_file'):
        with repository_session(task, task.repository, writable=True, deadline=lambda: 60) as session:
            session.write('app.py', 'value = 2\n')
            assert session.run_check('link', 10)['exit_code'] == 0
            raise RunLimit('max_tool_calls')
    assert (task.repository / 'app.py').read_bytes() == before
    assert not (task.repository / 'unsafe-link').exists()
