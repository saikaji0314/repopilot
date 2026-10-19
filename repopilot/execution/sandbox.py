"""按阶段创建隔离执行会话：文件工具与登记检查均不在宿主执行。"""

from contextlib import contextmanager
import json
from pathlib import Path
from threading import RLock
import time
from uuid import uuid4

from repopilot.execution import archive as archives
from repopilot.core.config import Task
from repopilot.execution.files import MAX_OUTPUT_BYTES, Repository
from repopilot.execution.transport import DockerTransport, SandboxError
from repopilot.storage.budget import RunLimit


class DockerSession:
    def __init__(self, task: Task, root: Path, *, writable: bool, deadline, trace=None):
        self.task, self.root, self.writable = task, root, writable
        self.config, self.deadline, self.trace = task.execution, deadline, trace
        self.transport = DockerTransport(self.config.wsl_distribution)
        self.name = f"repopilot-run-{uuid4().hex}"
        self.volume = self.name + "-work"
        self.initializer = self.name + "-init"
        self.before, self.lock = {}, RLock()
        self.volume_created = self.container_created = self.init_started = False
        source = Path(__file__).with_name('files.py').read_text(encoding='utf-8')
        worker = Path(__file__).with_name('worker.py').read_text(encoding='utf-8')
        self.worker = source + '\n' + worker

    def emit(self, event, **values):
        if self.trace:
            self.trace.event(event, backend='docker', **values)

    def prepare(self):
        self.before = archives.inventory(self.root, self.config.max_snapshot_bytes)
        # 只接受预装镜像；不在模型运行中下载依赖或更换镜像。
        info = json.loads(self.transport.run('image', 'inspect', self.config.image))[0]
        if self.config.image not in [*info.get('RepoDigests', []), info.get('Id')]:
            raise SandboxError('image_digest_not_available')
        self.emit('sandbox_preparing', image=self.config.image, container=self.name)
        self.volume_created = True
        self.transport.run('volume', 'create', '--label', 'repopilot.runtime=true', self.volume)
        mount = f'type=volume,src={self.volume},dst=/work,volume-nocopy'
        self.init_started = True
        self.transport.run('run', '--rm', '--name', self.initializer, '--network', 'none',
            '--label', 'repopilot.runtime=true', '--read-only', '--user', '0', '--cap-drop', 'ALL',
            '--cap-add', 'CHOWN', '--security-opt', 'no-new-privileges:true', '--memory', '64m',
            '--pids-limit', '16', '--mount', mount, '--entrypoint', 'python3', self.config.image,
            '-I', '-S', '-c', "import os; os.chown('/work',65532,65532)")
        self.init_started = False
        self.container_created = True
        self.transport.run('create', '--name', self.name, '--label', 'repopilot.runtime=true',
            '--network', 'none', '--user', '65532:65532', '--read-only', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges:true', '--cpus', str(self.config.cpus),
            '--memory', f'{self.config.memory_mb}m', '--memory-swap', f'{self.config.memory_mb}m',
            '--pids-limit', str(self.config.pids), '--ulimit', 'nofile=256:256', '--mount', mount,
            '--tmpfs', '/tmp:rw,nosuid,nodev,size=32m', '--workdir', '/work', '--entrypoint', 'python3',
            self.config.image, '-I', '-S', '-c', 'import time; time.sleep(3600)')
        self.transport.run('cp', '-a', '-', f'{self.name}:/work', data=archives.pack(self.before),
                           timeout=min(60, self.deadline()))
        self.transport.run('start', self.name)
        self.rpc('cwd', '.')
        self.emit('sandbox_ready', image=self.config.image, files=len(self.before))
        return self

    def rpc(self, action, *arguments):
        with self.lock:
            try:
                raw = self.transport.run('exec', '-i', self.name, 'python3', '-I', '-S',
                    '-c', self.worker, data=json.dumps({'action': action, 'arguments': arguments}).encode(),
                    timeout=min(30, self.deadline()))
            except SandboxError:
                info = getattr(self.transport, 'last_failure', None) or {}
                self.emit('sandbox_transport_error', action=action,
                          **{key: info.get(key) for key in
                             ('operation', 'returncode', 'category', 'stderr_bytes', 'stdout_bytes')})
                raise
            result = json.loads(raw)
            if not result.get('ok'):
                raise ValueError(result.get('error', 'file_operation_failed'))
            return result['result']

    def files(self):
        return iter(self.rpc('files'))

    def text(self, path):
        return self.rpc('text', path)

    def read(self, path, start_line, end_line):
        return self.rpc('read', path, start_line, end_line)

    def listing(self, path=".", file_glob="*"):
        return self.rpc('listing', path, file_glob)

    def search(self, query, path=".", file_glob="*"):
        return self.rpc('search', query, path, file_glob)

    def write(self, path, content):
        if not self.writable:
            raise ValueError('readonly_session')
        return self.rpc('write', path, content)

    def replace(self, path, old, new):
        if not self.writable:
            raise ValueError('readonly_session')
        return self.rpc('replace', path, old, new)

    def run_check(self, name, timeout):
        if name not in self.task.checks:
            raise ValueError('unknown_check')
        check = self.task.checks[name]
        with self.lock:
            cwd = self.rpc('cwd', check.cwd)
            argv = ['python3' if item == '{python}' else item for item in check.argv]
            started, timed_out, output_limited = time.monotonic(), False, False
            try:
                output, code = self.transport.run('exec', '--workdir', cwd,
                    '-e', 'PYTHONDONTWRITEBYTECODE=1', '-e', 'PYTHONIOENCODING=utf-8', self.name,
                    *argv, timeout=min(timeout, check.timeout_seconds, self.deadline()),
                    cap=self.config.max_output_bytes, allow_failure=True)
            except SandboxError as error:
                if str(error) not in {'docker_transport_timeout', 'docker_transport_output_limit'}:
                    raise
                timed_out = str(error) == 'docker_transport_timeout'
                output_limited = not timed_out
                output, code = str(error).encode(), 124 if timed_out else 125
            finally:
                # 正常结束也重启：仓库代码可能遗留脱离进程组的后台子进程。
                self.stop()
                self.transport.run('start', self.name)
            limited = len(output) > MAX_OUTPUT_BYTES
            if limited:
                marker = b'\n...[head and tail retained]...\n'
                half = (MAX_OUTPUT_BYTES - len(marker)) // 2
                output = output[:half] + marker + output[-half:]
            return {'name': name, 'exit_code': code, 'timed_out': timed_out,
                    'output_limited': output_limited, 'output': output.decode('utf-8', errors='replace'),
                    'truncated': limited or output_limited, 'duration_seconds': round(time.monotonic()-started, 3)}

    def stop(self):
        state = json.loads(self.transport.run('inspect', '--format', '{{json .State}}', self.name))
        if state['Running']:
            self.transport.run('kill', self.name)
        state = json.loads(self.transport.run('inspect', '--format', '{{json .State}}', self.name))
        if state['Running'] or state['Pid']:
            raise SandboxError('container_not_stopped')

    def finish(self):
        with self.lock:
            self.stop()
            data = self.transport.run('cp', f'{self.name}:/work/.', '-', timeout=60,
                cap=self.config.max_snapshot_bytes + archives.MAX_FILES * 4096 + 10240)
            after = archives.unpack(data, self.config.max_snapshot_bytes)
            changed = archives.apply(self.root, self.before, after, writable=self.writable,
                                     limit=self.config.max_snapshot_bytes)
            self.emit('sandbox_imported' if self.writable else 'sandbox_verified', changed_files=changed,
                      image=self.config.image)

    def close(self):
        errors = []
        for created, kind, name in [(self.init_started, 'init', self.initializer),
                                   (self.container_created, 'container', self.name),
                                   (self.volume_created, 'volume', self.volume)]:
            if created:
                try:
                    if kind == 'volume':
                        output, code = self.transport.run('volume', 'rm', name, allow_failure=True)
                    else:
                        output, code = self.transport.run('rm', '-f', name, allow_failure=True)
                    # 创建命令中途失败时，资源也可能根本不存在；只认明确的不存在响应。
                    missing = b'no such volume' if kind == 'volume' else b'no such container'
                    if code and missing not in output.lower():
                        errors.append(name)
                except (SandboxError, OSError):
                    errors.append(name)
        self.emit('sandbox_cleanup', complete=not errors)
        if errors:
            raise SandboxError('sandbox_cleanup_failed')


@contextmanager
def repository_session(task, root, *, writable, deadline, trace=None):
    if task.execution.backend == 'local':
        yield None
        return
    session = DockerSession(task, root, writable=writable, deadline=deadline, trace=trace)
    try:
        session.prepare()
        try:
            yield session
        except RunLimit as error:
            # 受控超限不丢弃已完成的编辑；仍停止容器并走相同的完整产物校验。
            # 非法产物会使finish失败，不能以“保存进度”为由绕过导入边界。
            if writable:
                session.finish()
                session.emit('sandbox_partial_checkpoint', reason=str(error))
            raise
        else:
            session.finish()
    finally:
        session.close()
