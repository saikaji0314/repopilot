"""从真实Git版本捕获文件事实；不保存模型叙述、审批决定或评测答案。"""

from hashlib import sha256
import os
from pathlib import Path
import re

from repopilot.execution.repository import Repository
from repopilot.storage.jobs import StateError
from repopilot.execution.workspace import head_commit, run_git

EXCLUDED = {'evals', 'oracle', 'patches', 'evaluation'}
MAX_EXCERPT_BYTES = 1600


class MemoryFault(StateError):
    pass


def repository_id(root):
    value = run_git(Path(root), 'rev-parse', '--git-common-dir').stdout.decode('utf-8').strip()
    common = Path(value)
    if not common.is_absolute():
        common = Path(root) / common
    return sha256(os.path.normcase(str(common.resolve())).encode()).hexdigest()


def allowed_path(path):
    value = Repository._guard(path).as_posix()
    if any(part.casefold() in EXCLUDED or part.casefold().startswith('.env') for part in Path(value).parts):
        raise MemoryFault('memory_source_denied')
    return value


def normalized(data):
    return data.decode('utf-8-sig').replace('\r\n', '\n').replace('\r', '\n')


def checked_text(root, path, commit=None):
    path = allowed_path(path)
    text = Repository(root).text(path)
    if commit is not None:
        committed = normalized(run_git(Path(root), 'show', f'{commit}:{path}').stdout)
        if committed != text:
            raise MemoryFault('memory_source_uncommitted')
    return text


def fingerprint(text):
    return sha256(text.encode('utf-8')).hexdigest()


def capture(root, path, start_line, end_line, dependencies=(), secrets=()):
    if not 1 <= start_line <= end_line or end_line - start_line >= 40:
        raise MemoryFault('memory_line_range_invalid')
    commit, path = head_commit(Path(root)), allowed_path(path)
    text = checked_text(root, path, commit)
    if end_line > len(text.splitlines()):
        raise MemoryFault('memory_line_range_invalid')
    excerpt = '\n'.join(text.splitlines()[start_line - 1:end_line])
    if not excerpt.strip() or len(excerpt.encode('utf-8')) > MAX_EXCERPT_BYTES:
        raise MemoryFault('memory_excerpt_invalid')
    if any(secret and secret in excerpt for secret in secrets) or re.search(
            r'(?i)(?:sk-[a-z0-9]{12,}|ghp_[a-z0-9]{12,}|github_pat_[a-z0-9_]+|'
            r'(?:api[_-]?key|token|password|secret)\s*[:=]\s*[\"\'][^\"\'\n]{8,}[\"\'])', excerpt):
        raise MemoryFault('memory_possible_secret')
    paths = list(dict.fromkeys([path, *dependencies]))
    if len(paths) > 8:
        raise MemoryFault('memory_too_many_dependencies')
    fingerprints = {allowed_path(p): fingerprint(checked_text(root, p, commit)) for p in paths}
    return {'kind': 'file_fact', 'path': path, 'start_line': start_line, 'end_line': end_line,
            'content': excerpt, 'source_commit': commit, 'dependencies': fingerprints}


def freshness(record, root, now):
    if record['status'] in {'revoked', 'deleted'}:
        return record['status']
    if record['expires_at'] <= now.isoformat():
        return 'expired'
    try:
        for path, expected in record['dependencies'].items():
            if fingerprint(checked_text(root, path)) != expected:
                return 'source_changed'
        run_git(Path(root), 'cat-file', '-e', record['source_commit'] + '^{commit}')
    except (OSError, ValueError, StateError, RuntimeError):
        return 'source_unavailable'
    return 'fresh'
