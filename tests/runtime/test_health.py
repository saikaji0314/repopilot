"""新旧心跳与坏记录均可解释，不改写持久化历史。"""

from datetime import datetime, timedelta, timezone
from contextlib import closing

import pytest

from repopilot.runtime.health import heartbeat_health
from repopilot.runtime.watcher import IssueQueue

NOW = datetime(2026, 10, 17, tzinfo=timezone.utc)


@pytest.mark.parametrize('age, status', [(0, 'running'), (30, 'running'), (31, 'stale'), (3600, 'stale')])
def test_running_heartbeat_expires(age, status):
    record = {'pid': 1, 'status': 'running', 'heartbeat_at': (NOW - timedelta(seconds=age)).isoformat()}
    result = heartbeat_health(record, now=NOW)
    assert result['status'] == status
    assert result['recorded_status'] == 'running'
    assert result['healthy'] == (status == 'running')
    assert result['age_seconds'] == age
    assert record['status'] == 'running'


@pytest.mark.parametrize('value', ['invalid', '2026-10-17T00:00:00', None])
def test_invalid_time_is_unknown(value):
    result = heartbeat_health({'pid': 1, 'status': 'running', 'heartbeat_at': value}, now=NOW)
    assert result['status'] == 'unknown' and not result['healthy']


def test_future_and_stopped_records_are_not_healthy():
    future = {'pid': 1, 'status': 'running', 'heartbeat_at': (NOW + timedelta(seconds=60)).isoformat()}
    assert heartbeat_health(future, now=NOW)['reason'] == 'clock_skew'
    stopped = future | {'status': 'stopped', 'heartbeat_at': (NOW - timedelta(days=1)).isoformat()}
    assert heartbeat_health(stopped, now=NOW)['status'] == 'stopped'


def test_queue_status_does_not_rewrite_expired_record(tmp_path):
    queue = IssueQueue(tmp_path)
    assert queue.health()['status'] == 'not_started'
    with closing(queue.store.connect()) as db, db:
        db.execute("INSERT INTO watcher_runtime VALUES (1, 123, 'running', '2000-01-01T00:00:00Z')")
    assert queue.health()['status'] == 'stale'
    with closing(queue.store.connect()) as db:
        assert db.execute('SELECT status FROM watcher_runtime').fetchone()[0] == 'running'
