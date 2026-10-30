"""Desktop boundaries, persisted replay and real runtime decisions; no paid model or forge."""
import json
import sys
from pathlib import Path
import threading
import time

import pytest

fastapi = pytest.importorskip('fastapi')
from fastapi.testclient import TestClient

from repopilot.desktop.api import create_app
from repopilot.desktop.read_model import DesktopReader
from repopilot.desktop.control import request_pause, pause_pending
from repopilot.execution.repository import run_check
from repopilot.execution.telemetry import command_output
from repopilot.core.config import Check
from repopilot.storage.jobs import JobStore
from tests.runtime.test_workflow import candidate
from tests.runtime.test_review_loop import setup, FakeModel

TOKEN = 'desktop-test-session-' + 'x' * 32
HEADERS = {'Authorization': 'Bearer ' + TOKEN}


def client(root):
    return TestClient(create_app(root, TOKEN), base_url='http://127.0.0.1')


def seed(root):
    store = JobStore(root)
    return store.create({'run_id': 'demo', 'task': {'id': 'demo', 'repository': 'sample'},
                         'status': 'running', 'phase': 'implement', 'human_requests': [],
                         'settings': {'api_key': 'must-never-be-returned'}, 'control': {}})


def test_loopback_auth_and_origin_and_empty_state_are_read_only(tmp_path):
    root = tmp_path / 'not-created'
    api = client(root)
    assert api.get('/health').status_code == 200
    assert api.get('/api/runs').status_code == 401
    assert api.get('/api/runs', headers=HEADERS | {'Origin': 'https://attacker.example'}).status_code == 403
    assert api.get('/api/runs', headers=HEADERS | {'Host': 'attacker.example'}).status_code == 403
    assert api.get('/api/runs', headers=HEADERS).json() == {'runs': [], 'queue': []}
    assert not root.exists()


def test_projection_omits_private_state_and_redacts_outputs(tmp_path):
    seed(tmp_path)
    directory = tmp_path / 'runs/demo'; directory.mkdir(parents=True)
    records = [{'event': 'model_finished', 'messages': 'private chain of thought'},
               {'event': 'tool_finished', 'action_id': 'a1', 'result': 'token=abc-secret password=hunter2', 'time': 'now'}]
    (directory / 'events.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in records))
    api = client(tmp_path)
    detail = api.get('/api/runs/demo', headers=HEADERS)
    assert detail.status_code == 200
    assert 'settings' not in detail.json() and 'must-never-be-returned' not in detail.text
    events, _, _ = DesktopReader(tmp_path).events('demo')
    assert len(events) == 1 and events[0]['action_id'] == 'a1'
    assert 'abc-secret' not in json.dumps(events) and 'hunter2' not in json.dumps(events)
    assert 'model_finished' not in json.dumps(events)


def test_event_cursor_handles_partial_utf8_append_without_loss(tmp_path):
    seed(tmp_path)
    directory = tmp_path / 'runs/demo'; directory.mkdir(parents=True)
    path = directory / 'events.jsonl'
    data = json.dumps({'event': 'command_output', 'action_id': 'a', 'output': '第一行\n第二行'}, ensure_ascii=False).encode()
    path.write_bytes(data[:30])
    reader = DesktopReader(tmp_path)
    assert reader.events('demo') == ([], 0, False)
    with path.open('ab') as f: f.write(data[30:] + b'\n')
    events, cursor, reset = reader.events('demo')
    assert len(events) == 1 and events[0]['output'] == '第一行\n第二行' and not reset
    assert reader.events('demo', cursor)[0] == []
    path.write_bytes(b'')
    assert reader.events('demo', cursor) == ([], 0, True)


def test_pause_rejects_old_revision_and_only_sets_separate_control(tmp_path):
    state = seed(tmp_path)
    api = client(tmp_path)
    assert api.post('/api/runs/demo/pause', headers=HEADERS, json={'revision': 9}).status_code == 409
    assert not pause_pending(tmp_path, 'demo')
    reply = api.post('/api/runs/demo/pause', headers=HEADERS, json={'revision': 0})
    assert reply.json()['boundary'] == 'phase_checkpoint'
    assert pause_pending(tmp_path, 'demo')
    assert JobStore(tmp_path).get('demo')['status'] == 'running'
    assert JobStore(tmp_path).get('demo')['_revision'] == state['_revision']


def test_api_decision_uses_real_fixed_commit_gate(tmp_path):
    result, root = candidate(tmp_path)
    api = client(root); state = JobStore(root).get(result['run_id'])
    endpoint = '/api/runs/' + state['run_id'] + '/decision'
    body = {'revision': state['_revision'], 'commit': '0' * 40, 'approved': True, 'reason': 'reviewed'}
    assert api.post(endpoint, headers=HEADERS, json=body).json()['detail'] == 'stale_approval'
    body['commit'] = state['reviewed_commit']; body['revision'] = state['_revision'] - 1
    assert api.post(endpoint, headers=HEADERS, json=body).json()['detail'] == 'checkpoint_conflict'
    body['revision'] = state['_revision']
    assert api.post(endpoint, headers=HEADERS, json=body).json()['status'] == 'approved'
    assert JobStore(root).get(state['run_id'])['approval']['commit'] == state['reviewed_commit']


def test_pause_is_acknowledged_after_checkpoint_not_during_phase(tmp_path, monkeypatch):
    from repopilot.orchestration import workflow, session
    task, settings, root = setup(tmp_path)
    step = session.RunSession.step
    def pause_after_step(self):
        phase = step(self)
        assert self.state['in_flight'] is None
        request_pause(root, self.state['run_id'])
        return phase
    monkeypatch.setattr(session.RunSession, 'step', pause_after_step)
    result = workflow.start(task, settings, root, model=FakeModel(responses=[]))
    assert result['status'] == 'paused' and result['stop_reason'] == 'operator_pause'
    state = JobStore(root).get(result['run_id'])
    assert state['in_flight'] is None and state['workspace_snapshot']
    assert not pause_pending(root, state['run_id'])


def test_real_command_output_arrives_before_exit_on_windows_and_posix(tmp_path):
    task, _, _ = setup(tmp_path)
    task = task.model_copy(update={'checks': {'slow': Check(argv=[sys.executable, '-u', '-c',
        "import time; print('first', flush=True); time.sleep(.6); print('last', flush=True)"])}})
    chunks, result = [], []
    def worker():
        with command_output(lambda text: chunks.append((time.monotonic(), text))):
            result.append(run_check(task, 'slow', 5))
    thread = threading.Thread(target=worker); thread.start()
    deadline = time.monotonic() + 4
    while not chunks and time.monotonic() < deadline: time.sleep(.02)
    early = bool(chunks) and thread.is_alive()
    thread.join(timeout=6)
    assert early, chunks
    assert result[0]['exit_code'] == 0 and result[0]['output'].splitlines() == ['first', 'last']
    assert ''.join(t for _, t in chunks).splitlines() == ['first', 'last']
