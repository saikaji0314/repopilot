"""有界 Docker CLI 传输；不把候选命令交给宿主 shell。"""

import os
import subprocess
from threading import Event, Thread
import time

from .workspace import WorkspaceError


class SandboxError(WorkspaceError):
    pass


class DockerTransport:
    def __init__(self, distribution=None):
        if os.name == "nt":
            if not distribution:
                raise SandboxError("docker_on_windows_requires_wsl_distribution")
            self.prefix = ["wsl.exe", "-d", distribution, "-u", "root", "--exec", "docker"]
        else:
            if distribution:
                raise SandboxError("wsl_transport_requires_windows")
            self.prefix = ["docker"]

    def run(self, *args, data=b"", timeout=30, cap=1_000_000, allow_failure=False):
        keep = {"PATH", "SYSTEMROOT", "WINDIR", "HOME", "USERPROFILE", "LOCALAPPDATA", "LANG", "LC_ALL"}
        env = {k: v for k, v in os.environ.items() if k.upper() in keep}
        process = subprocess.Popen([*self.prefix, *args], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, shell=False)
        overflow, buffers = Event(), [bytearray(), bytearray()]

        def read(stream, index, limit):
            try:
                while chunk := stream.read(65536):
                    room = max(0, limit - len(buffers[index]))
                    buffers[index].extend(chunk[:room])
                    if len(chunk) > room:
                        overflow.set()
                        break
            finally:
                stream.close()

        def write():
            try:
                process.stdin.write(data)
                process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
            finally:
                process.stdin.close()

        threads = [Thread(target=read, args=(process.stdout, 0, cap), daemon=True),
                   Thread(target=read, args=(process.stderr, 1, 65536), daemon=True),
                   Thread(target=write, daemon=True)]
        for thread in threads:
            thread.start()
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                if overflow.is_set() or time.monotonic() >= deadline:
                    raise SandboxError("docker_transport_output_limit" if overflow.is_set() else "docker_transport_timeout")
                time.sleep(0.02)
            for thread in threads:
                thread.join(1)
            if overflow.is_set():
                raise SandboxError("docker_transport_output_limit")
            if any(thread.is_alive() for thread in threads):
                raise SandboxError("docker_transport_pipe_not_closed")
            if allow_failure:
                return bytes(buffers[0]) + b"\n" + bytes(buffers[1]), process.returncode
            if process.returncode:
                # stderr可能含来源数据，错误只传播固定代码；原始Docker认证不进入模型事件。
                raise SandboxError(f"docker_{args[0]}_failed")
            return bytes(buffers[0])
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
