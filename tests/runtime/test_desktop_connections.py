"""Desktop configuration and delivery against synthetic GitHub HTTP, never real writes."""
from datetime import datetime, timezone, timedelta
from contextlib import closing
import json
import os
from types import SimpleNamespace

import httpx
import pytest
pytest.importorskip('fastapi')
pytest.importorskip('cryptography')
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from repopilot.desktop.api import create_app
from repopilot.desktop.comments import CommentDraft, IssueDiscussion
from repopilot.desktop.read_model import DesktopReader
from repopilot.desktop.settings import SettingsStore, SettingsUpdate, WindowsVault
from repopilot.storage.jobs import JobStore, StateError
from repopilot.collaboration.comments import select_reply, marker

TOKEN = 'isolated-desktop-test-' + 'x' * 32
HEADERS = {'Authorization': 'Bearer ' + TOKEN}


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith(('REPOPILOT_', 'GITHUB_', 'DS_')) or key == 'GH_TOKEN':
            monkeypatch.delenv(key)
    store = SettingsStore(tmp_path / 'runtime', env_file=tmp_path / 'empty.env', vault=Fernet(Fernet.generate_key()))
    return store


@pytest.fixture(scope='module')
def pem():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()


def update(**values):
    return SettingsUpdate.model_validate({'revision': 0, 'github': {'source': 'desktop', 'app_id': '1', 'installation_id': '2',
        'owner': 'owner', 'repository': 'owner/repo'}, 'model': {'source': 'desktop'}, **values})


def api(store):
    return TestClient(create_app(store.root, TOKEN, settings_store=store), base_url='http://127.0.0.1')


def seed(store):
    JobStore(store.root).create({'run_id': 'issue-test', 'status': 'needs_input', 'phase': 'implement',
        'task': {'id': 'test', 'repository': 'fixture'}, 'issue': {'repository': 'owner/repo', 'number': 1,
        'url': 'https://github.com/owner/repo/issues/1'}, 'human_requests': []})


def synthetic_github(store, *, actor='repo-app[bot]', uncertain=False, visible=True):
    comments, posts = [], []
    def handle(request):
        path = request.url.path
        if path == '/app':
            return httpx.Response(200, json={'id': 1, 'slug': 'repo-app'})
        if path == '/app/installations/2/access_tokens':
            return httpx.Response(201, json={'token': 'synthetic-installation', 'expires_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()})
        if path == '/user':
            return httpx.Response(200, json={'login': actor})
        if path == '/repos/owner/repo':
            return httpx.Response(200, json={'full_name': 'owner/repo', 'permissions': {'issues': 'write'}})
        assert path == '/repos/owner/repo/issues/1/comments'
        if request.method == 'GET':
            return httpx.Response(200, json=comments if visible else [])
        posts.append(json.loads(request.content)['body'])
        comment = {'id': 321, 'html_url': 'https://github.com/owner/repo/issues/1#issuecomment-321',
            'body': posts[-1], 'user': {'login': actor, 'type': 'Bot' if actor.endswith('[bot]') else 'User'},
            'created_at': '2026-10-30T00:00:00Z'}
        comments.append(comment)
        if uncertain:
            raise httpx.ReadTimeout('synthetic reply lost', request=request)
        return httpx.Response(201, json=comment)
    store.transport = httpx.MockTransport(handle)
    return comments, posts


def test_settings_no_secret_readback_keep_clear_and_revision(isolated, pem):
    response = api(isolated).post('/api/settings', headers=HEADERS, json=update(private_key=pem, user_token='synthetic-personal-token', model_api_key='synthetic-model-key').model_dump())
    assert response.status_code == 200
    assert response.json()['github']['private_key_configured'] is True
    for secret in [pem, 'synthetic-personal-token', 'synthetic-model-key']:
        assert secret not in response.text and secret.encode() not in isolated.path.read_bytes()
    assert isolated.runtime_settings().api_key.get_secret_value() == 'synthetic-model-key'
    assert isolated.save(update(revision=1))['model']['api_key_configured']
    with pytest.raises(StateError, match='desktop_settings_revision_conflict'):
        isolated.save(update(revision=1))
    public = isolated.save(update(revision=2, clear_private_key=True, clear_user_token=True, clear_model_api_key=True))
    assert not public['github']['private_key_configured'] and not public['model']['api_key_configured']
    assert not isolated.env_file.exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows user DPAPI')
def test_real_windows_dpapi_and_restart_fail_closed(tmp_path, pem, monkeypatch):
    store = SettingsStore(tmp_path / 'dpapi', env_file=tmp_path / 'absent.env')
    result = store.save(update(private_key=pem, model_api_key='synthetic-windows-key'))
    raw = store.path.read_bytes()
    assert b'synthetic-windows-key' not in raw and b'PRIVATE KEY' not in raw
    restarted = SettingsStore(store.root, env_file=store.env_file)
    assert restarted.load()['secrets']['model_api_key'] == 'synthetic-windows-key'
    assert result['secure_storage'] == 'windows-current-user-dpapi'
    store.path.write_bytes(b'corrupt encrypted data')
    with pytest.raises(StateError, match='cannot_decrypt'):
        store.public()
    assert store.path.read_bytes() == b'corrupt encrypted data'


def test_invalid_secrets_are_not_echoed_or_saved(isolated):
    client = api(isolated)
    response = client.post('/api/settings', headers=HEADERS, json=update().model_dump() | {'model_api_key': 's' * 5000})
    assert response.status_code == 422 and 's' * 20 not in response.text
    response = client.post('/api/settings', headers=HEADERS, json=update(private_key='not-an-rsa-private-key').model_dump())
    assert response.status_code == 409 and 'not-an-rsa' not in response.text
    assert not isolated.path.exists()
    assert client.get('/api/settings').status_code == 401


def test_environment_is_explicit_and_not_overwritten(isolated, pem, monkeypatch):
    isolated.env_file.write_text('GITHUB_AUTH=token\nGITHUB_TOKEN=synthetic-env-token\nREPOPILOT_OWNER=owner\nDS_API_KEY=synthetic-env-model\n')
    before = isolated.env_file.read_bytes()
    assert isolated.public()['model']['api_key_configured']
    isolated.save(update(private_key=pem, user_token='synthetic-desktop-token', model_api_key='synthetic-desktop-model'))
    isolated.save(update(revision=1, github={'source': 'environment'}, model={'source': 'environment'}))
    assert isolated.runtime_settings().api_key.get_secret_value() == 'synthetic-env-model'
    with closing(isolated.github('owner/repo', sender='user')) as client:
        assert client.token == 'synthetic-env-token'
    assert isolated.env_file.read_bytes() == before


def test_readonly_connection_and_no_inference_or_budget_reset(isolated, pem):
    isolated.save(update(private_key=pem, model_api_key='synthetic-key'))
    (isolated.root / 'budget-sentinel').write_bytes(b'existing ledger')
    comments, posts = synthetic_github(isolated)
    client = api(isolated)
    result = client.post('/api/settings/check-github', headers=HEADERS, json={'repository': 'owner/repo'})
    assert result.status_code == 200 and result.json()['login'] == 'repo-app[bot]'
    assert result.json()['write_verified'] is False and not posts
    result = client.post('/api/settings/check-model', headers=HEADERS, json={})
    assert result.status_code == 200 and result.json()['inference_verified'] is False
    assert (isolated.root / 'budget-sentinel').read_bytes() == b'existing ledger'


def test_comment_send_exact_identity_once_and_conflict(isolated, pem):
    isolated.save(update(private_key=pem)); seed(isolated)
    comments, posts = synthetic_github(isolated)
    client = api(isolated)
    listed = client.get('/api/runs/issue-test/comments', headers=HEADERS)
    assert listed.status_code == 200 and listed.json()['user_token_configured'] is False
    draft = {'client_id': 'desktop-test-comment-001', 'body': '补充普通讨论', 'sender': 'bot'}
    first = client.post('/api/runs/issue-test/comments', headers=HEADERS, json=draft)
    assert first.status_code == 200 and first.json()['comment']['author'] == 'repo-app[bot]'
    second = client.post('/api/runs/issue-test/comments', headers=HEADERS, json=draft)
    assert second.status_code == 200 and second.json()['reused'] and len(posts) == 1
    conflict = client.post('/api/runs/issue-test/comments', headers=HEADERS, json=draft | {'body': 'changed'})
    assert conflict.status_code == 409 and len(posts) == 1
    assert JobStore(isolated.root).get('issue-test')['status'] == 'needs_input'


@pytest.mark.parametrize('visible', [True, False])
def test_lost_comment_response_reconciles_and_never_blindly_reposts(isolated, pem, visible):
    isolated.save(update(private_key=pem)); seed(isolated)
    comments, posts = synthetic_github(isolated, uncertain=True, visible=visible)
    service = IssueDiscussion(DesktopReader(isolated.root), isolated)
    draft = CommentDraft(client_id='desktop-test-comment-002', body='one ordinary comment')
    with pytest.raises(StateError, match='uncertain'):
        service.send('issue-test', draft)
    if visible:
        assert service.send('issue-test', draft)['reused']
    else:
        with pytest.raises(StateError, match='uncertain'):
            service.send('issue-test', draft)
    assert len(posts) == 1


def test_wrong_personal_identity_cannot_post(isolated, pem):
    isolated.save(update(private_key=pem, user_token='synthetic-personal-token')); seed(isolated)
    comments, posts = synthetic_github(isolated, actor='someone-else')
    with pytest.raises(StateError, match='must_match_registered_owner'):
        IssueDiscussion(DesktopReader(isolated.root), isolated).send('issue-test', CommentDraft(client_id='desktop-test-comment-003', body='hello', sender='user'))
    assert posts == []


def test_personal_ordinary_comment_is_not_formal_agent_answer(isolated, pem):
    isolated.save(update(private_key=pem, user_token='synthetic-personal-token')); seed(isolated)
    comments, posts = synthetic_github(isolated, actor='owner')
    sent = IssueDiscussion(DesktopReader(isolated.root), isolated).send('issue-test', CommentDraft(client_id='desktop-test-comment-004', body='普通讨论', sender='user'))
    request = SimpleNamespace(id='question-1', questions=['question'], summary='summary')
    accepted, ignored = select_reply([{'id': 1, 'body': marker(request)}, sent['comment']], request, owner='owner', batch_id=1)
    assert accepted is None and ignored[0]['ignored_reason'] == 'repopilot_comment'


def test_copied_comment_marker_from_another_author_is_not_reused(isolated, pem):
    isolated.save(update(private_key=pem)); seed(isolated)
    comments, posts = synthetic_github(isolated)
    comments.append({'id': 55, 'html_url': 'https://github.com/owner/repo/issues/1#issuecomment-55',
        'body': '<!-- repopilot-desktop-comment:desktop-test-comment-005 -->\n\nhello',
        'user': {'login': 'someone-else', 'type': 'User'}, 'created_at': '2026-10-30T00:00:00Z'})
    result = IssueDiscussion(DesktopReader(isolated.root), isolated).send('issue-test', CommentDraft(client_id='desktop-test-comment-005', body='hello'))
    assert not result['reused'] and result['comment']['author'] == 'repo-app[bot]' and len(posts) == 1


def test_comment_delivery_lock_covers_separate_service_instances(isolated, pem):
    from repopilot.storage.jobs import job_lock
    isolated.save(update(private_key=pem)); seed(isolated)
    comments, posts = synthetic_github(isolated)
    with job_lock(isolated.root, 'comment-desktop-test-comment-006'):
        service = IssueDiscussion(DesktopReader(isolated.root), isolated)
        with pytest.raises(StateError, match='job_is_running'):
            service.send('issue-test', CommentDraft(client_id='desktop-test-comment-006', body='hello'))
    assert not posts
