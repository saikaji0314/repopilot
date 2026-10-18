# Docker 隔离执行

可信操作者可在任务 YAML 中选择 `execution.backend: docker`。文件读写、搜索、实现自检、返工、独立验证和审核检查都走容器会话；镜像、Docker 或文件交换失败时终止运行，不自动转为本机执行。默认仍为 `local`，旧任务及旧检查点保持原行为。

## 配置与准备

Linux 控制端使用本机 Docker CLI。Windows 控制端使用指定 WSL 发行版内的 Docker Engine，需先启动该服务；本次实际验证环境为 Ubuntu-24.04。两者都需要操作者拥有 Docker 控制权限。

```yaml
execution:
  backend: docker
  # 已预装的 repository@sha256 摘要，也接受本机构建得到的 sha256:完整镜像ID。
  image: python@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
  wsl_distribution: Ubuntu-24.04  # Linux 控制端省略此项
  memory_mb: 256
  cpus: 1
  pids: 64
  max_output_bytes: 1000000
  max_snapshot_bytes: 20000000
checks:
  unit:
    argv: ["{python}", "-m", unittest, discover]
    timeout_seconds: 30
```

把此段加入已有任务（仍需 `id / request / repository`）。原有 `run / issue / watch / resume` 入口无需改变。任务开始后冻结执行配置；恢复读取原检查点，不从修改后的 YAML 静默切换后端或镜像。镜像必须已存在且匹配不可变摘要或镜像 ID；候选运行期间不拉取镜像、不安装依赖。

工具镜像需包含 Python 3.10 或更高版本的 `python3` 标准库供文件协议使用，无需安装 LangChain 或模型 SDK。`{python}` 替换为容器中的 `python3`，其他 argv 原样执行；登记命令、cwd 和依赖需适用于该 Linux 镜像。不能直接使用 Windows 的 `.venv` 或绝对解释器路径。项目依赖解释器可通过登记的 Linux 绝对路径单独指定，无须与文件服务解释器相同。

Python／Node 的 amd64 示例见 [PythonNode.Dockerfile](../../examples/runtime/docker/PythonNode.Dockerfile)。它基于两个固定摘要的 Debian bookworm 镜像，提供 Python 与 Node，不提供 npm。依赖准备属于可信操作，应在运行前构建专用镜像；不要把凭证写入镜像。构建后将完整镜像 ID 填入任务，不能把可变 tag 直接交给运行时：

```bash
docker build --pull=false --network=none -t repopilot-toolchain -f examples/runtime/docker/PythonNode.Dockerfile examples/runtime/docker
docker image inspect --format '{{.Id}}' repopilot-toolchain
```

首次准备基础镜像需单独拉取上述摘要；Windows 上可在 WSL 的仓库路径运行构建命令。示例依赖 Linux amd64；ARM、Windows 原生项目及不同依赖组合尚需各自准备和验证。

## 阶段与权限边界

```mermaid
flowchart LR
    H[宿主：模型、预算、记忆、状态与审批] --> S[筛选源码快照]
    S --> C[新容器：文件工具与登记检查]
    C --> K[停止全部容器进程]
    K --> V[完整校验普通文件归档]
    V --> I[导入托管 worktree]
    I --> G[宿主 Git 提交与 SHA 绑定]
    G --> R[新容器独立验证与审核]
```

- 实现和返工各用一个新卷及新容器，成功结束后导入文件，再由宿主 anchor。独立验证和审核各用新容器，任何源码变化都会阻断流程；检查产生的缓存不导入。审核文件工具没有写权限，但登记检查仍能写其临时副本，退出时再核对。
- 宿主 `.git`、`.env`、`.local`、home、账本和 Docker socket 不挂载；按现有敏感路径规则筛选快照，符号链接不传入。模型和 GitHub 凭证留控制面。容器只接收筛选源码、可信镜像环境与固定协议源码。
- 候选 UID/GID 为 65532，根文件系统只读，删除全部 capabilities，设置 no-new-privileges、禁网、CPU/内存/进程/文件描述符上限，保留 Engine 默认 seccomp。仅 `/work` 命名卷和 32 MiB 的 `/tmp` 可写。可信初始化容器只负责卷归属，不运行仓库代码。
- 每次登记检查结束后停止并重启整个候选容器，即便正常退出也清除后台子进程。检查不能依赖跨命令存活的服务；需要服务时，应在同一登记命令内启动、使用并结束。
- 检查直接由宿主 Docker CLI `exec` 获取退出码，文件协议由 `python3 -I -S` 执行可信源码。容器中的仓库代码不能通过替换同目录 Python 模块来接管文件协议。宿主不运行候选脚本。

检查 stdout 流式限额默认为 1 MB，stderr 限额为 64 KiB；任一超限停止检查，报告退出 125。超时报告 124。保留给模型及验证报告的检查输出最多约 8 KB。超时／超限后仍停止整个容器，失败不会当成成功或回退本机。

## 文件交换、恢复与限制

停止容器后由 Docker 导出归档。宿主不使用 `extractall`：先完整解析、校验全部条目和宿主未被外部改动，才写入允许的文件。拒绝上溯／绝对路径、Windows 盘符与设备名、大小写碰撞、文件与目录冲突、敏感路径、软硬链接、设备及 FIFO、重复条目和超额内容。拒绝修改 `.gitattributes` 与 `.gitmodules`，文件与目录之间的类型转换也会在写入前拒绝。

快照默认总内容 20 MB，单文件 4 MB、最多 4,000 文件；归档条目及传输也有限额。文件工具仍有更小的 UTF-8 读取／编辑限额。`__pycache__ / .pytest_cache / .mypy_cache / .ruff_cache` 被忽略；其他构建产物若不在敏感目录中，可能随源码导入，应由任务检查与人工 diff 审核。权限归一为普通文件 0644／可执行 0755，空目录不保留。

正常退出或可捕获异常会移除本阶段容器和卷，并记录 `sandbox_preparing / sandbox_ready / sandbox_imported / sandbox_verified / sandbox_cleanup` 事件。容器名在 preparing 事件中，资源带 `repopilot.runtime=true` 标签。阶段完成后可按原机制暂停恢复；阶段中断的容器改动可能未导回宿主，不能声称逐工具恢复。硬杀、系统崩溃和 Docker 失联后的残留资源仍需核查，不提供自动扫描回收；清理失败会阻断运行并保留失败事件。

受控的预算、调用次数或活跃时间超限（RunLimit）是一个明确例外：可写阶段在重新抛出原超限前，先停止容器，按相同归档／宿主快照规则完整校验，再导入合法的部分改动并记录 `sandbox_partial_checkpoint`。失败状态和中断阶段仍保留，不会自动提交、恢复、审核或发布；非法产物会阻断导入。其他异常、准备失败和硬杀不承诺保留尚未导出的容器编辑。

这仍是可信仓库的本机执行后端。Docker daemon 是受信高权限控制面，宿主 Git 仍信任已登记仓库的现有配置、钩子和过滤器；没有隔离宿主管理员。普通容器共享 Linux 内核，尚无 microVM、rootless 或 gVisor 后端。工作卷没有磁盘配额；输出与导出限额不能阻止仓库代码先写满宿主磁盘。归档校验通过后的单文件替换是原子的，但跨文件导入不是事务，磁盘故障或硬杀可能留下部分改动，需人工核对后 recover。

真实执行验证见 [VALIDATION-11](VALIDATION-11.md)；Docker Sandboxes 本机失败及前置资源探针见 [VALIDATION-09](VALIDATION-09.md)。[增量14](VALIDATION-14.md)记录真实模型单题失败及受控超限的产物保存修复；尚无 Docker 后端真实 Issue 到 PR 成功结论，也未做不可信租户或逃逸攻击评估。
