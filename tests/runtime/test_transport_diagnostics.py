"""执行失败可定位，但错误正文、命令与敏感来源不能进入事件。"""

from pathlib import Path
import sys

import pytest

from repopilot.execution.sandbox import DockerSession
from repopilot.execution.transport import DockerTransport, SandboxError
from repopilot.core.config import ExecutionConfig, Task


def test_transport_records_numeric_failure_without_exposing_stderr():
    transport = DockerTransport.__new__(DockerTransport)
    transport.prefix = [sys.executable, '-c',
                        "import sys; sys.stderr.write('OCI runtime exec failed: synthetic-secret'); sys.exit(7)"]
    with pytest.raises(SandboxError, match='^docker_exec_failed$') as caught:
        transport.run('exec')
    assert 'synthetic-secret' not in str(caught.value)
    assert transport.last_failure['returncode'] == 7
    assert transport.last_failure['category'] == 'runtime_exec_error'
    assert transport.last_failure['stderr_bytes'] > 0
    assert 'synthetic-secret' not in repr(transport.last_failure)
    transport.prefix = [sys.executable, '-c', "print('ok')"]
    assert transport.run('exec').strip() == b'ok' and transport.last_failure is None


def test_rpc_emits_safe_failure_metadata_and_propagates_error(tmp_path):
    task = Task(id='diagnostic', request='read source', repository=tmp_path,
                execution=ExecutionConfig(backend='docker', image='sha256:'+'a'*64,
                                          wsl_distribution='Ubuntu-24.04'))
    events = []
    class Trace:
        def event(self, event, **values): events.append({'event': event, **values})
    class BrokenTransport:
        last_failure = {'operation': 'exec', 'returncode': 1, 'category': 'worker_exception',
                        'stderr_bytes': 100, 'stdout_bytes': 0, 'raw': 'synthetic-secret'}
        def run(self, *args, **kwargs): raise SandboxError('docker_exec_failed')
    session = DockerSession(task, Path(tmp_path), writable=False, deadline=lambda: 10, trace=Trace())
    session.transport = BrokenTransport()
    with pytest.raises(SandboxError, match='docker_exec_failed'):
        session.rpc('read', 'app.py', 1, 10)
    assert events == [{'event': 'sandbox_transport_error', 'backend': 'docker', 'action': 'read',
                       'operation': 'exec', 'returncode': 1, 'category': 'worker_exception',
                       'stderr_bytes': 100, 'stdout_bytes': 0}]
    assert 'synthetic-secret' not in repr(events)
