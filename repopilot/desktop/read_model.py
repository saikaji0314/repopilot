"""Bounded, read-only projections. Never expose model messages, settings or raw state."""

from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3

from repopilot.desktop.control import pause_pending
from repopilot.storage.jobs import StateError

RUN_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,119}\Z')
SECRET = re.compile(r'(?i)(bearer\s+\S+|(?:gh[pousr]_|github_pat_|sk-)[A-Za-z0-9_-]{8,}|'
                    r'(?:api[_-]?key|token|password|secret)\s*[=:]\s*["\']?[^\s,"\']+)')


def clean(value):
    if isinstance(value, str):
        value = re.sub(r'-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----',
                       '[REDACTED PRIVATE KEY]', value, flags=re.S)
        return SECRET.sub('[REDACTED]', value)[:32000]
    if isinstance(value, dict):
        return {str(k): '[REDACTED]' if re.search(r'(?i)(password|secret|api.?key|token)', str(k))
                else clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clean(v) for v in value[:1000]]
    return value


def decode(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return {'output': value}
    return value if isinstance(value, dict) else {}


class DesktopReader:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def db(self):
        return sqlite3.connect((self.root / 'jobs.sqlite3').as_uri() + '?mode=ro', uri=True, timeout=5)

    def state(self, run_id):
        if not RUN_ID.fullmatch(run_id):
            raise StateError('invalid_run_id')
        if not (self.root / 'jobs.sqlite3').is_file():
            raise StateError('job_not_found')
        with closing(self.db()) as db:
            row = db.execute('SELECT state,revision,updated_at FROM jobs WHERE id=?', (run_id,)).fetchone()
        if not row:
            raise StateError('job_not_found')
        return json.loads(row[0]) | {'_revision': row[1], '_updated_at': row[2]}

    def runs(self):
        if not (self.root / 'jobs.sqlite3').is_file():
            return []
        with closing(self.db()) as db:
            rows = db.execute('SELECT state,revision,updated_at FROM jobs ORDER BY updated_at DESC LIMIT 100').fetchall()
        return [self.summary(json.loads(s) | {'_revision': rev, '_updated_at': updated}) for s, rev, updated in rows]

    def summary(self, state):
        task, issue = state.get('task') or {}, state.get('issue') or {}
        return clean({'id': state['run_id'], 'title': issue.get('title') or task.get('title') or task.get('id') or state['run_id'],
                      'repository': issue.get('repository') or Path(task.get('repository') or '.').name,
                      'number': issue.get('number'), 'url': self.safe_url(issue.get('url')),
                      'status': state.get('status', 'unknown'), 'phase': state.get('phase', 'unknown'),
                      'revision': state['_revision'], 'updated_at': state.get('_updated_at'),
                      'created_at': state.get('created_at'), 'candidate': state.get('reviewed_commit'),
                      'requirements_revision': state.get('requirements_revision', 0),
                      'branch': (state.get('publication') or {}).get('branch') or state['run_id']})

    @staticmethod
    def safe_url(url):
        from urllib.parse import urlsplit
        try:
            parts = urlsplit(url or '')
            return url if parts.scheme == 'https' and parts.hostname and not parts.username and not parts.password else None
        except ValueError:
            return None

    def artifact(self, run_id, filename):
        # Only fixed runtime artifacts, never a path chosen by the renderer.
        path = self.root / 'runs' / run_id / filename
        if not path.resolve().is_relative_to(self.root) or path.is_symlink():
            raise StateError('artifact_path_denied')
        if not path.is_file():
            return {}
        with path.open('rb') as stream:
            data = stream.read(2_000_001)
        if len(data) > 2_000_000:
            return {'truncated': True}
        try:
            return json.loads(data)
        except ValueError:
            return {}

    def events(self, run_id, cursor=0):
        self.state(run_id)
        path = self.root / 'runs' / run_id / 'events.jsonl'
        if not path.resolve().is_relative_to(self.root) or path.is_symlink():
            raise StateError('artifact_path_denied')
        if not path.is_file():
            return [], 0, False
        records = []
        with path.open('rb') as stream:
            size = stream.seek(0, 2)
            reset = cursor > size
            stream.seek(0 if reset else cursor)
            for _ in range(500):
                start = stream.tell()
                line = stream.readline(256_001)
                if not line:
                    break
                if len(line) > 256_000:
                    raise StateError('trace_record_too_large')
                if not line.endswith(b'\n'):
                    stream.seek(start)
                    break  # incomplete append: do not consume it
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                kind = event.get('event', '')
                if kind in {'tool_started', 'tool_finished', 'tool_failed', 'command_output',
                            'verify_started', 'verify_finished', 'pause_requested', 'operator_paused'}:
                    # No model_started / model_finished or internal conversation data.
                    allowed = {'time', 'event', 'phase', 'name', 'args', 'result', 'error_type',
                               'action_id', 'agent_id', 'subtask_id', 'duration_seconds', 'output',
                               'exit_code', 'timed_out', 'error', 'truncated'}
                    projected = {k: v for k, v in event.items() if k in allowed}
                    if 'result' in projected:
                        projected['result'] = decode(projected['result'])
                    records.append(clean(projected) | {'id': str(stream.tell())})
            return records, stream.tell(), reset

    def detail(self, run_id):
        state = self.state(run_id)
        changes = self.artifact(run_id, 'changes.json')
        requests = [{k: r.get(k) for k in ('id', 'kind', 'status', 'summary', 'questions', 'version', 'context')}
                    for r in state.get('human_requests', []) if r.get('status') == 'pending']
        verified = state.get('verified') or []
        verdict = (state.get('review_report') or {}).get('verdict')
        stage_status = {
            'Planner': 'success' if state.get('plan') else 'idle',
            'Implementer': 'success' if (state.get('report') or {}).get('status') == 'implemented' else 'idle',
            'Verifier': ('success' if all(c.get('exit_code') == 0 and not c.get('error') for c in verified) else 'failed') if verified else 'idle',
            'Reviewers': 'success' if verdict == 'approved' else 'waiting' if verdict else 'idle',
        }
        return clean({**self.summary(state), 'in_flight': state.get('in_flight'),
                      'stop_reason': state.get('stop_reason'), 'pause_requested': pause_pending(self.root, run_id),
                      'verified': verified, 'review': state.get('review_report'), 'stage_status': stage_status,
                      'plan': state.get('plan'), 'requests': requests, 'approval': state.get('approval'),
                      'changes': {**{k: changes.get(k) for k in ('additions', 'deletions', 'diff', 'truncated')},
                                  'changed_files': [f['path'] for f in changes.get('files', []) if isinstance(f, dict) and 'path' in f]},
                      'capabilities': {'pause': state.get('status') == 'running',
                                       'decide': state.get('status') == 'awaiting_approval',
                                       'answer': any(r['kind'] == 'clarification' for r in requests),
                                       'publish': False, 'requirements_change': False,
                                       'github_comments': str((state.get('issue') or {}).get('url') or '').startswith('https://github.com/')}})

    def queue(self):
        if not (self.root / 'jobs.sqlite3').is_file():
            return []
        with closing(self.db()) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='issue_queue'").fetchone():
                return []
            return clean([dict(zip(('id', 'repository', 'status', 'run_id', 'error'), row)) for row in db.execute(
                'SELECT issue_key,repository,status,run_id,error FROM issue_queue ORDER BY created_at DESC LIMIT 100')])
