"""Ordinary Issue discussion with a durable, identity-bound delivery record."""
from contextlib import closing
from hashlib import sha256
import json
import sqlite3
from threading import RLock
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from repopilot.delivery.forge import ForgeError, parse_issue_url
from repopilot.desktop.read_model import clean
from repopilot.storage.jobs import StateError, timestamp, job_lock


class CommentDraft(BaseModel):
    model_config = ConfigDict(extra='forbid')
    client_id: str = Field(pattern=r'^[a-zA-Z0-9-]{16,80}$')
    body: str = Field(min_length=1, max_length=8000)
    sender: Literal['bot', 'user'] = 'bot'


class IssueDiscussion:
    def __init__(self, reader, settings):
        self.reader, self.settings = reader, settings
        self.lock = RLock()

    def target(self, run_id):
        state = self.reader.state(run_id)
        issue = state.get('issue') or {}
        parsed = parse_issue_url(issue.get('url'))
        if (parsed['provider'] != 'github' or parsed['host'] != 'github.com' or parsed['repository'].casefold() != str(issue.get('repository', '')).casefold()
                or parsed['number'] != issue.get('number')):
            raise StateError('github_issue_identity_mismatch')
        return parsed['repository'], parsed['number'], issue['url']

    def list(self, run_id):
        repository, number, url = self.target(run_id)
        with closing(self.settings.github(repository)) as client:
            login = client.viewer()
            comments = client.issue_comments(repository, number)
        config = self.settings.public()['github']
        return {'comments': clean(comments), 'issue_url': url, 'login': login,
                      'default_sender': 'bot' if login.endswith('[bot]') else 'user',
                      'owner': config['owner'], 'user_token_configured': config['user_token_configured']}

    def db(self):
        self.reader.root.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.reader.root / 'desktop-comments.sqlite3', timeout=10)
        db.execute('CREATE TABLE IF NOT EXISTS comments (id TEXT PRIMARY KEY, payload_hash TEXT NOT NULL, '
                   'actor TEXT NOT NULL, status TEXT NOT NULL, result TEXT, created_at TEXT NOT NULL)')
        return db

    @staticmethod
    def public_comment(comment):
        if not isinstance(comment.get('id'), int) or not comment.get('url'):
            raise StateError('github_comment_result_requires_reconciliation')
        return clean(comment)

    def send(self, run_id, draft: CommentDraft):
        repository, number, url = self.target(run_id)
        text = draft.body.strip()
        if not text or '<!-- repopilot-' in text:
            raise StateError('ordinary_comment_body_invalid')
        marker = f'<!-- repopilot-desktop-comment:{draft.client_id} -->'
        # Prefix is intentionally excluded by the existing formal-reply router,
        # including when a registered owner sends an ordinary desktop comment.
        body = marker + '\n\n' + text
        digest = sha256(json.dumps([run_id, url, draft.sender, text], ensure_ascii=False).encode()).hexdigest()
        with self.lock, job_lock(self.reader.root, 'comment-' + draft.client_id), closing(self.settings.github(repository, sender=draft.sender)) as client:
            actor = client.viewer()
            owner = self.settings.public()['github']['owner']
            if draft.sender == 'bot' and not actor.endswith('[bot]'):
                raise StateError('github_bot_identity_required_use_personal_sender')
            if draft.sender == 'user' and (not owner or actor.casefold() != owner.casefold() or actor.endswith('[bot]')):
                raise StateError('github_personal_identity_must_match_registered_owner')
            with closing(self.db()) as db, db:
                previous = db.execute('SELECT payload_hash,actor,status,result FROM comments WHERE id=?', (draft.client_id,)).fetchone()
                if previous and (previous[0] != digest or previous[1].casefold() != actor.casefold()):
                    raise StateError('comment_draft_identity_or_body_changed')
                if previous and previous[2] == 'sent':
                    return {'status': 'sent', 'comment': json.loads(previous[3]), 'reused': True}
                if not previous:
                    db.execute('INSERT INTO comments VALUES (?,?,?,\'ready\',NULL,?)', (draft.client_id, digest, actor, timestamp()))
            # Reconcile an earlier timeout before doing any further write.
            for item in client.issue_comments(repository, number):
                if (item.get('body', '').replace('\r\n', '\n') == body.replace('\r\n', '\n')
                        and str(item.get('author', '')).casefold() == actor.casefold()):
                    comment = self.public_comment(item)
                    with closing(self.db()) as db, db:
                        db.execute('UPDATE comments SET status=\'sent\',result=? WHERE id=?', (json.dumps(comment), draft.client_id))
                    return {'status': 'sent', 'comment': comment, 'reused': True}
            if previous and previous[2] in {'sending', 'uncertain'}:
                raise StateError('comment_delivery_uncertain_check_github_before_new_draft')
            with closing(self.db()) as db, db:
                db.execute('UPDATE comments SET status=\'sending\' WHERE id=?', (draft.client_id,))
            try:
                comment = self.public_comment(client.add_issue_comment(repository, number, body))
                if str(comment.get('author', '')).casefold() != actor.casefold():
                    raise StateError('github_comment_identity_mismatch')
            except Exception as exc:
                definite = isinstance(exc, ForgeError) and exc.status is not None and 400 <= exc.status < 500 and exc.status != 408
                with closing(self.db()) as db, db:
                    db.execute('UPDATE comments SET status=? WHERE id=?', ('failed' if definite else 'uncertain', draft.client_id))
                if definite:
                    raise
                raise StateError('comment_delivery_uncertain_retry_same_draft') from None
            with closing(self.db()) as db, db:
                db.execute('UPDATE comments SET status=\'sent\',result=? WHERE id=?', (json.dumps(comment), draft.client_id))
            return {'status': 'sent', 'comment': comment, 'reused': False}
