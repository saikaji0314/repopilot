"""在专用 Docker 资源上做真实执行探针；不是 RepoPilot 的生产沙箱后端。"""

from __future__ import annotations

import argparse
import difflib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid


class Probe:
    def __init__(self, output: Path):
        self.output = output
        self.prefix = f"repopilot-probe-{uuid.uuid4().hex[:12]}"
        self.events: list[dict] = []
        self.results: list[dict] = []
        self.containers: list[str] = []
        self.volumes: list[str] = []

    def command(self, *args: str, timeout: int = 30) -> subprocess.CompletedProcess:
        started = time.monotonic()
        try:
            result = subprocess.run(
                ["docker", *args], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            self.events.append({"argv": ["docker", *args], "timed_out": True,
                                "seconds": round(time.monotonic() - started, 3)})
            raise
        # 所有探针输出本身有界；日志只保留尾部。不是生产流式输出限额实现。
        self.events.append({
            "argv": ["docker", *args], "exit_code": result.returncode,
            "seconds": round(time.monotonic() - started, 3),
            "stdout_tail": result.stdout[-4096:], "stderr_tail": result.stderr[-4096:],
        })
        return result

    def require(self, name: str, condition: bool, **evidence) -> None:
        self.results.append({"name": name, "passed": bool(condition), **evidence})
        print(f"{'PASS' if condition else 'FAIL'} {name}", flush=True)
        if not condition:
            raise RuntimeError(f"探针失败：{name}")

    def ok(self, *args: str, timeout: int = 30) -> str:
        result = self.command(*args, timeout=timeout)
        if result.returncode:
            raise RuntimeError(f"Docker 命令失败：{args[0]}: {result.stderr[-1000:]}")
        return result.stdout.strip()

    def execute(self, name: str, program: str, code: str) -> subprocess.CompletedProcess:
        flag = "-c" if program == "python" else "-e"
        return self.command("exec", name, program, flag, code)

    def prepare(self, suffix: str, image: str) -> str:
        self.ok("pull", image, timeout=600)
        metadata = json.loads(self.ok("image", "inspect", image))[0]
        digest = metadata["RepoDigests"][0]
        name = f"{self.prefix}-{suffix}"
        volume = f"{name}-work"
        self.ok("volume", "create", "--label", f"repopilot.probe={self.prefix}", volume)
        self.volumes.append(volume)
        # 可信初始化只获得 CHOWN，随后候选容器完全删除 capabilities。
        self.ok("run", "--rm", "--network", "none", "--read-only", "--user", "0",
                "--cap-drop", "ALL", "--cap-add", "CHOWN",
                "--security-opt", "no-new-privileges:true", "--memory", "64m",
                "--mount", f"type=volume,src={volume},dst=/work,volume-nocopy",
                digest, "chown", "65532:65532", "/work")
        self.ok(
            "create", "--name", name, "--label", f"repopilot.probe={self.prefix}",
            "--network", "none", "--user", "65532:65532", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
            "--cpus", "1", "--memory", "128m", "--memory-swap", "128m",
            "--pids-limit", "32", "--ulimit", "nofile=128:128",
            "--mount", f"type=volume,src={volume},dst=/work,volume-nocopy",
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=16m", "--workdir", "/work",
            digest, "sleep", "600",
        )
        self.containers.append(name)
        self.ok("start", name)
        self.results.append({"name": f"{suffix}_image", "passed": True, "digest": digest})
        return name

    def run(self) -> None:
        self.results.append({"name": "engine", "passed": True,
                             "version": self.ok("version", "--format", "{{.Server.Version}}")})
        python = self.prepare("python", "python:3.12-slim-bookworm")
        node = self.prepare("node", "node:22-bookworm-slim")
        with tempfile.TemporaryDirectory(prefix=self.prefix) as temporary:
            before, after, check = self.check_workspaces(python, node, temporary)
            self.check_network_and_privileges(python)
            self.check_resources(python)
            self.check_lifecycle(python, temporary, before, after, check)

    def check_workspaces(self, python: str, node: str, temporary: str):
        sentinel = Path(temporary) / "host-only.txt"
        sentinel.write_text(uuid.uuid4().hex)
        code = (
            "import os,pathlib; "
            f"assert not pathlib.Path({str(sentinel)!r}).exists(); "
            "assert not pathlib.Path('/mnt/c').exists(); "
            "assert 'REPOPILOT_PROBE_SECRET' not in os.environ; "
            "assert os.getuid()==65532; print('isolated')"
        )
        result = self.execute(python, "python", code)
        self.require("host_path_and_environment", result.returncode == 0,
                     stdout=result.stdout.strip())
        self.ok("exec", python, "python", "-c",
                "from pathlib import Path; Path('/work/task-a-only').write_text('marker')")
        result = self.execute(node, "node",
                              "if(require('fs').existsSync('/work/task-a-only')) process.exit(9)")
        self.require("separate_task_volume", result.returncode == 0)

        for name, program, code in (
            (python, "python", "import sys; print('stdout-ok'); print('stderr-ok',file=sys.stderr); sys.exit(7)"),
            (node, "node", "console.log('stdout-ok'); console.error('stderr-ok'); process.exit(7)"),
        ):
            result = self.execute(name, program, code)
            self.require(f"{program}_exit_and_streams", result.returncode == 7
                         and "stdout-ok" in result.stdout and "stderr-ok" in result.stderr)

        before = "def add(a, b):\n    return a - b\n"
        after = "def add(a, b):\n    return a + b\n"
        source = Path(temporary) / "calc.py"
        source.write_text(before)
        os.chown(source, 65532, 65532)
        self.ok("cp", "--archive", str(source), f"{python}:/work/calc.py")
        check = "import sys; sys.dont_write_bytecode=True; from calc import add; assert add(2,3)==5"
        result = self.execute(python, "python", check)
        self.require("python_baseline_fails", result.returncode == 1)
        result = self.execute(python, "python", f"from pathlib import Path; Path('/work/calc.py').write_text({after!r})")
        self.require("candidate_edit", result.returncode == 0)
        result = self.execute(python, "python", check)
        self.require("python_fixed_passes", result.returncode == 0)
        result = self.execute(node, "node", "require('assert').strictEqual(2+3,5)")
        self.require("javascript_check_passes", result.returncode == 0)
        return before, after, check

    def check_network_and_privileges(self, python: str):
        code = (
            "import socket,sys; "
            "s=socket.socket(); s.settimeout(2); "
            "r=s.connect_ex(('1.1.1.1',443)); print(r); sys.exit(0 if r else 1)"
        )
        result = self.execute(python, "python", code)
        self.require("outbound_tcp_denied", result.returncode == 0, errno=result.stdout.strip())
        result = self.command("exec", "--user", "0", python, "python", "-c",
                              "open('/etc/repopilot-probe','w').write('x')")
        self.require("root_filesystem_write_denied", result.returncode != 0
                     and "Read-only file system" in result.stderr)
        result = self.execute(python, "python", "from pathlib import Path; s=Path('/proc/self/status').read_text(); assert 'NoNewPrivs:\\t1' in s and 'CapEff:\\t0000000000000000' in s and 'Seccomp:\\t2' in s")
        self.require("privilege_and_seccomp_flags", result.returncode == 0)

    def check_resources(self, python: str):
        result = self.execute(python, "python", "import json,pathlib; p=pathlib.Path('/sys/fs/cgroup'); print(json.dumps({k:(p/k).read_text().strip() for k in ['memory.max','cpu.max','pids.max']}))")
        limits = json.loads(result.stdout)
        self.require("cgroup_limits", result.returncode == 0 and limits["memory.max"] == "134217728"
                     and limits["pids.max"] == "32"
                     and limits["cpu.max"].split()[0] == limits["cpu.max"].split()[1],
                     limits=limits)
        result = self.execute(python, "python", "import subprocess\nps=[]\ntry:\n for i in range(64): ps.append(subprocess.Popen(['sleep','5']))\nexcept BlockingIOError:\n print('pids-limited',len(ps))\nfinally:\n for p in ps: p.terminate()\n for p in ps: p.wait()")
        self.require("process_limit_enforced", result.returncode == 0 and "pids-limited" in result.stdout,
                     result=result.stdout.strip())
        count = "from pathlib import Path; print(dict(x.split() for x in Path('/sys/fs/cgroup/memory.events').read_text().splitlines())['oom_kill'])"
        old_oom = int(self.execute(python, "python", count).stdout)
        result = self.execute(python, "python", "x=bytearray(256*1024*1024)")
        new_oom = int(self.execute(python, "python", count).stdout)
        self.require("memory_oom_enforced", result.returncode == 137 and new_oom > old_oom,
                     exit_code=result.returncode, oom_kill_delta=new_oom-old_oom)

    def check_lifecycle(self, python: str, temporary: str, before: str, after: str, check: str):
        try:
            self.command("exec", python, "sh", "-c", "sleep 120 & wait", timeout=2)
        except subprocess.TimeoutExpired:
            # 超时后停止整个容器，不能只杀 docker exec 客户端。
            self.ok("kill", python)
            state = json.loads(self.ok("inspect", "--format", "{{json .State}}", python))
            self.require("timeout_stops_container", not state["Running"] and state["Pid"] == 0)
        else:
            self.require("timeout_stops_container", False)
        # 仓库代码停止之后才导出；这里仅导出已知探针文件，不是通用不可信归档导入器。
        exported = Path(temporary) / "exported.py"
        self.ok("cp", f"{python}:/work/calc.py", str(exported))
        self.require("stopped_artifact_export", exported.read_text() == after)
        patch = "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                            fromfile="a/calc.py", tofile="b/calc.py"))
        (self.output / "candidate.patch").write_text(patch)
        self.ok("start", python)
        result = self.execute(python, "python", check)
        # Docker top 要求 ps 输出包含 PID 列，否则会在解析前失败。
        processes = self.ok("top", python, "-eo", "pid,args")
        self.require("restart_retains_files_not_old_child", result.returncode == 0 and "sleep 120" not in processes)

    def cleanup(self) -> None:
        for name in reversed(self.containers):
            result = self.command("rm", "-f", name)
            self.results.append({"name": f"remove_{name}", "passed": result.returncode == 0})
        for name in reversed(self.volumes):
            result = self.command("volume", "rm", name)
            self.results.append({"name": f"remove_{name}", "passed": result.returncode == 0})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("请在专用 Linux／WSL Docker worker 中运行，不在 Windows 宿主裸跑夹具")
    if os.geteuid() != 0:
        parser.error("当前探针需要 root 控制进程准备卷和文件所有权；候选容器使用 UID 65532")
    args.output.mkdir(parents=True, exist_ok=False)
    probe = Probe(args.output)
    os.environ["REPOPILOT_PROBE_SECRET"] = uuid.uuid4().hex
    error = None
    try:
        probe.run()
    except (Exception, KeyboardInterrupt) as exc:
        error = f"{type(exc).__name__}: {exc}"
        print(error, file=sys.stderr, flush=True)
    finally:
        try:
            probe.cleanup()
        except Exception as exc:
            error = error or f"清理失败：{exc}"
        passed = error is None and all(item["passed"] for item in probe.results)
        report = {"passed": passed, "error": error, "results": probe.results,
                  "events": probe.events, "unverified": [
                      "production_execution_backend", "hostile_artifact_import",
                      "disk_quota", "streaming_output_limit", "sandbox_escape_resistance",
                  ]}
        (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
