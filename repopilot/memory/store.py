"""本地仓库事实存储、人工生命周期与有界关键词检索。"""

from contextlib import closing
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3

from repopilot.memory.facts import MemoryFault, capture, freshness, repository_id

MAX_CONTEXT_BYTES = 6000


def tokens(text):
    words = re.findall(r'[a-zA-Z_][a-zA-Z_0-9]{1,}|[\u4e00-\u9fff]{2}', text.casefold())
    return set(words) - {'py', 'js', 'the', 'and', 'src'}


class MemoryStore:
    def __init__(self, root):
        self.path = Path(root).resolve() / 'memory.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS facts (id TEXT PRIMARY KEY, repository_id TEXT NOT NULL, '
                       'status TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS facts_repository ON facts(repository_id, status)')

    def connect(self):
        return sqlite3.connect(self.path, timeout=15)

    def get(self, root, identifier):
        with closing(self.connect()) as db:
            row = db.execute('SELECT payload FROM facts WHERE id=? AND repository_id=?',
                             (identifier, repository_id(root))).fetchone()
        if row is None:
            raise MemoryFault('memory_not_found')
        return json.loads(row[0])

    def remember(self, root, path, start_line, end_line, *, dependencies=(), source_run=None,
                 candidate=False, secrets=()):
        fact = capture(root, path, start_line, end_line, dependencies, secrets)
        namespace = repository_id(root)
        identity = [namespace, fact['path'], start_line, end_line, fact['dependencies']]
        identifier = sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        now = datetime.now(timezone.utc)
        record = {**fact, 'id': identifier, 'repository_id': namespace,
                  'status': 'candidate' if candidate else 'active', 'source_run': source_run,
                  'created_at': now.isoformat(), 'expires_at': (now + timedelta(days=30)).isoformat()}
        if not candidate:
            record['decision'] = {'action': 'remember', 'actor': 'local_operator', 'at': now.isoformat(),
                                  'reason': 'operator_registered_file_fact'}
        with closing(self.connect()) as db, db:
            # 候选和事件重放不得复活已经撤销／删除的同一事实。
            db.execute('INSERT OR IGNORE INTO facts VALUES (?, ?, ?, ?, ?)',
                       (identifier, namespace, record['status'], json.dumps(record, ensure_ascii=False), now.isoformat()))
        return self.get(root, identifier)

    def records(self, root, status=None):
        condition, parameters = '', [repository_id(root)]
        if status is not None:
            condition = ' AND status=?'
            parameters.append(status)
        with closing(self.connect()) as db:
            rows = db.execute('SELECT payload FROM facts WHERE repository_id=? '
                              + condition + ' ORDER BY created_at DESC, id LIMIT 200', parameters).fetchall()
        return [json.loads(row[0]) for row in rows]

    def inspect(self, root):
        now = datetime.now(timezone.utc)
        return [{**record, 'freshness': freshness(record, root, now)}
                if record['status'] not in {'revoked', 'deleted'} else
                {k: v for k, v in record.items() if k != 'content'} for record in self.records(root)]

    def decide(self, root, identifier, action, reason):
        if not reason.strip() or len(reason) > 1000 or action not in {'confirm', 'revoke', 'forget'}:
            raise MemoryFault('memory_decision_invalid')
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT payload FROM facts WHERE id=? AND repository_id=?',
                             (identifier, repository_id(root))).fetchone()
            if row is None:
                raise MemoryFault('memory_not_found')
            record = json.loads(row[0])
            self._decision(record, root, action, reason)
            db.execute('UPDATE facts SET status=?,payload=? WHERE id=?',
                       (record['status'], json.dumps(record, ensure_ascii=False), identifier))
        return record

    @staticmethod
    def _decision(record, root, action, reason):
        now = datetime.now(timezone.utc)
        if action == 'confirm':
            if record['status'] in {'revoked', 'deleted'}:
                raise MemoryFault('memory_cannot_resurrect')
            # 显式确认可延长TTL，但仍必须重新核对文件内容与来源。
            probe = record | {'expires_at': (now + timedelta(days=30)).isoformat()}
            if freshness(probe, root, now) != 'fresh':
                raise MemoryFault('memory_source_changed')
            record.update(status='active', expires_at=probe['expires_at'], validated_at=now.isoformat())
        else:
            record['status'] = 'revoked' if action == 'revoke' else 'deleted'
            if action == 'forget':
                record['content'] = ''
        record['decision'] = {'action': action, 'reason': reason, 'at': now.isoformat(), 'actor': 'local_operator'}

    def search(self, root, query):
        terms = set(sorted(tokens(query))[:32])
        ranked = []
        for record in self.records(root, status='active'):
            score = 3 * len(terms & tokens(record['path'])) + len(terms & tokens(record['content']))
            if score:
                ranked.append((score, record))
        ranked.sort(key=lambda item: (-item[0], item[1]['id']))
        entries, omitted = [], []
        now = datetime.now(timezone.utc)
        for score, record in ranked[:20]:
            reason = freshness(record, root, now)
            if reason != 'fresh':
                omitted.append({'id': record['id'], 'reason': reason})
                continue
            entry = self.entry(record) | {'score': score}
            if len(entries) < 5 and len(json.dumps(entries + [entry], ensure_ascii=False).encode()) <= MAX_CONTEXT_BYTES:
                entries.append(entry)
        return {'repository_id': repository_id(root), 'entries': entries, 'omitted': omitted,
                'max_bytes': MAX_CONTEXT_BYTES, 'scanned_limit': 200}

    @staticmethod
    def entry(record):
        return {key: record[key] for key in ('id', 'kind', 'path', 'start_line', 'end_line', 'content',
                                             'source_commit', 'source_run', 'dependencies')}

    def view(self, root, snapshot):
        entries, omitted = [], []
        if snapshot['repository_id'] != repository_id(root):
            raise MemoryFault('memory_repository_mismatch')
        for entry in snapshot['entries']:
            record = self.get(root, entry['id'])
            reason = freshness(record, root, datetime.now(timezone.utc))
            if record['status'] != 'active' or reason != 'fresh':
                omitted.append({'id': entry['id'], 'reason': reason if reason != 'fresh' else record['status']})
            else:
                entries.append(entry)
        return {'repository_id': snapshot['repository_id'], 'entries': entries, 'omitted': omitted}
