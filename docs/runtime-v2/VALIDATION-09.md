# 增量9：隔离执行前置验证

验证日期：2026-10-17 至 2026-10-18。结论：Docker Sandboxes 的原生 Windows 路线与 WSL KVM 前提分别失败；操作者选择在 Ubuntu 内改验普通 Docker Engine，17项行为探针通过。产品执行后端尚未接入，原运行时仍在宿主执行。各路线不自动互相降级。

## 验证条件

- Windows x64，系统 build 26200。
- Docker Sandboxes `v0.48.0 5caf923e0587d376848f6f388eca3e0f5ca00e2d`，来自官方 WinGet 包 `Docker.sbx`。
- 官方安装器 `DockerSandboxes.msi` 的 WinGet SHA256 校验通过：`889d42213238a1d8db50245e5fd56e92ad52fda73400e900c6fa74d8fc08e718`。
- 启用 `HypervisorPlatform -All`，重启后 `sbx diagnose --format json` 的虚拟化检查从不可用变为通过：`WHvCapabilityCodeHypervisorPresent is true`。
- 宿主没有安装 Docker Desktop／Engine；按[当前安装文档](https://docs.docker.com/ai/sandboxes/install/)，独立 `sbx` 不要求这两者。Docker OAuth 由操作者完成，不向 RepoPilot 提供账号密码。

本轮没有调用模型、启动 Issue 监听或创建云资源。模型费用账本及既有任务工作区未改动。原始诊断和命令结果仅保存在本机被忽略的证据目录，不随代码提交。

## 真实命令与结果

```powershell
sbx daemon start --detach --policy deny-all
sbx login
sbx create --name repopilot-spike-09 --cpus 2 --memory 2g --skills off shell
```

daemon 启动和登录成功。创建没有传入工作区路径，目标是无宿主目录挂载的本地 shell 沙箱；CPU、内存和 skills 参数仅为请求值，因创建失败，尚未验证实际生效。

登录后 `sbx diagnose --format json` 的14项检查全部通过，但下面的真实创建仍然失败。这说明安装诊断不能替代执行验收。

首次创建在本地镜像元数据检查阶段失败：

```text
error: inspect local snapshot metadata: request failed:
500 Internal Server Error: failed to inspect local image metadata
```

显式增加 `--pull always` 再试，选择官方 `docker/sandbox-templates:shell-docker` 模板，在镜像准备阶段失败：

```text
error: request failed: 500 Internal Server Error:
pull failed for image "docker/sandbox-templates:shell-docker"
```

daemon 日志将失败定位到它自身的内部 socket：

```text
dial unix <LOCAL_STATE>/sandboxd/docker.sock:
connect: An invalid argument was supplied.
```

这里的 socket 属于 Docker Sandboxes 内部后端；不是要求操作者额外启动宿主 Docker Engine 的证据。失败后 `sbx ls --json` 返回空列表，尚未创建任务沙箱。

## 诊断与判断边界

[官方仓库 Issue #157](https://github.com/docker/sbx-releases/issues/157) 存在相同 Windows 内部 socket 症状，维护者曾确认复现。其他用户也报告在诊断健康时，实际镜像操作仍失败。本机结果与其症状一致，但没有定位到上游源代码根因，不能断言所有 Windows 机器或所有版本都不可用。

该 Issue 中有用户报告在 WSL2 内安装 Linux 版可绕过。此报告不是本机验证结果；本机实际尝试见下一节。没有通过清空状态、反复重装、扩大网络权限或使用 nightly 来掩盖失败。

## WSL2＋Ubuntu 的实际尝试

启用 `VirtualMachinePlatform` 并再次重启后，Microsoft WSL `2.7.13.0` 正常运行。默认 Ubuntu 安装渠道长时间没有进度、未注册发行版；停止该次安装客户端后，以下官方渠道成功安装：

```powershell
wsl --install -d Ubuntu-24.04 --no-launch --web-download
wsl -d Ubuntu-24.04 -u root --exec sh -c 'uname -r; cat /etc/os-release'
```

实际发行版是 Ubuntu `24.04.5 LTS`，内核 `6.18.33.2-microsoft-standard-WSL2`。首次只读环境检查使用发行版 root，没有创建或索取 Linux 用户密码，也没有让候选仓库代码以 root 执行。

宿主为 AMD Ryzen，匹配的 KVM 模块探针结果如下：

```text
grep -c -w svm /proc/cpuinfo
0
modprobe kvm_amd
modprobe: ERROR: could not insert 'kvm_amd': Operation not supported
ls -l /dev/kvm
ls: cannot access '/dev/kvm': No such file or directory
```

在原本不存在的用户 `.wslconfig` 中仅增加 `[wsl2] nestedVirtualization=true`，执行 `wsl --shutdown` 后复测，仍然没有 `svm` 或 `/dev/kvm`。内核日志为 `Hyper-V: Nested features: 0x0` 和 `kvm_amd: SVM not supported by CPU`。重启的是新装 WSL 环境，没有再次重启 Windows。

本机 Windows build `26200.9711`、AMD CPU 和上述症状与[微软已确认的嵌套虚拟化回归](https://github.com/microsoft/WSL/issues/41917)一致；这只是症状匹配，本机未进一步证实其内部 HCS 查询根因。关联 [PR #41942](https://github.com/microsoft/WSL/pull/41942) 增加检测失败告警，明确不修复底层 Windows 回归，不能把 Issue 关闭视为当前环境已恢复。

[Docker Linux 安装要求](https://docs.docker.com/ai/sandboxes/install/)明确依赖 KVM，因此在此前提失败后没有继续安装 Linux `docker-sbx`、重新登录或创建 VM。没有卸载 Windows 更新或关闭安全功能。

## Docker Sandboxes 路线结果

| 项目 | 结果 |
| --- | --- |
| CLI 安装、版本、安装器校验 | 通过 |
| Windows 虚拟化重启生效 | 通过 |
| daemon 启动、Docker 登录 | 通过 |
| 官方 shell 镜像准备与沙箱创建 | 失败，内部 socket 错误 |
| WSL2 与 Ubuntu 24.04 安装启动 | 通过 |
| WSL2 内 KVM／嵌套虚拟化前提 | 失败，无 svm 与 /dev/kvm |
| Sandboxes 内命令、文件、网络及资源限制 | 未验证，创建前提失败 |

## 普通 Docker 容器路线

操作者明确选择在已安装 Ubuntu 内改验 Docker Engine。这条路线共享 WSL 的 Linux 内核，不需要在 WSL 内运行 KVM 虚拟机，不能称为每任务 microVM。

### 环境及准备

- Ubuntu 24.04.5，Docker Engine／CLI 29.9.1，containerd 2.4.2，runc 1.5.2。
- 使用[Docker 官方 Ubuntu apt 源](https://docs.docker.com/engine/install/ubuntu/)，最小安装 `docker-ce docker-ce-cli containerd.io`，没有安装 Desktop、buildx 或 Compose。核心包匹配签名索引的 SHA256。
- WSL NAT 模式无法使用宿主 localhost 代理，连接官方源时重置；切换微软支持的 `networkingMode=mirrored` 后恢复。镜像拉取仍需为受信 daemon 配置网络；本机只保存经校验无认证字段的回环代理，配置权限0600。该代理属于依赖准备，不进入候选容器。
- 两个官方镜像在拉取后固定 RepoDigest 再创建容器。本次 Python 为 `python@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258`，Node 为 `node@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392`。重新拉取可变tag可能得到不同digest，应保留各次报告，不能假设版本相同。

### 配置与操作协议

[可重复探针](../../scripts/probe_container_execution.py)只使用独立夹具及专用资源，不调用模型或GitHub，不接触真实仓库代码。控制脚本在 Linux／WSL worker 内运行，需要root准备文件所有权和操作Docker；这不是给模型开放root工具。

每个候选容器使用独立命名卷、UID/GID65532、只读根文件系统、`cap-drop ALL`、`no-new-privileges`、Docker默认seccomp、`network none`，不挂载宿主目录或Docker socket。限制为1 CPU、128 MiB且无额外swap、32 PID、128个打开文件，`/tmp`为16 MiB tmpfs。卷初始化是单独的可信短任务，仅增加CHOWN能力；工作卷显式`volume-nocopy`，避免镜像目录元数据覆盖准备好的所有者。

候选代码在容器内执行和编辑；超时后停止整个容器并核对PID为0，停止后才导出已知文件。再启动时应保留卷内容、旧子进程消失。结束时仅清理本次唯一名称的容器和卷，不执行全局prune。

### 真实结果

2026-10-18的最终脚本返回0，`report.passed=true`。报告包含17项行为检查、3项版本／镜像记录及4项资源清理记录，共24项；它们不是24项独立安全证明。

| 行为 | 实际观察 |
| --- | --- |
| 宿主路径及环境边界 | 虚构宿主诱饵不可见，`/mnt/c`不可见，测试环境变量不继承；没有尝试读取真实凭证 |
| 跨任务工作区 | Python卷写入标记，Node卷看不到该标记 |
| Python／Node进程协议 | 成功命令通过；显式退出7时原样返回，stdout/stderr分别收集 |
| 编辑与验证 | Python缺陷夹具先失败，容器内修改后检查通过；JavaScript断言通过 |
| 外网探针 | 到1.1.1.1:443的TCP连接被拒绝；没有据此声称所有协议和网络攻击均已验证 |
| 根文件系统及权限 | root写根文件系统也被只读挂载拒绝；候选UID65532，CapEff为0、NoNewPrivs为1、Seccomp为2 |
| cgroup配置 | memory.max=134217728，cpu.max=100000 100000，pids.max=32；未做CPU负载吞吐测试 |
| 进程上限 | 尝试创建64个子进程，在达到上限前收到资源不足，并回收已创建进程 |
| OOM | 256 MiB分配触发退出137，memory.events中的oom_kill增加1 |
| 超时与恢复 | 带子进程的命令超时后停止整个容器，Running=false、Pid=0；重启后检查仍通过且旧sleep子进程不存在 |
| 导出与清理 | 停止后导出文件符合预期并生成补丁；两容器和两工作卷清理成功，标签资源清单为空 |

前期失败全部保留：未配置daemon代理时镜像拉取超时；空卷copy-up导致写权限失败；旧版`docker top -eo args`因没有PID列而失败。最后改为`-eo pid,args`后通过；没有通过放宽候选容器权限修复这些失败。本机保存逐次report、命令退出码、输出尾部与candidate.patch，原始数据不提交。

### 复跑

在专用Linux worker上安装上述Docker Engine及Python3，先启动Engine。以下命令需要Docker控制权限，并会拉取公开镜像；`--output`必须是不存在的新目录：

```bash
sudo python3 scripts/probe_container_execution.py --output /var/tmp/repopilot-probe-new-run
```

探针自身输出有界，日志只截取尾部；没有实现可承受任意候选程序的生产流式输出限额。还未完成：通用恶意归档／链接产物导入、工作卷磁盘配额、进程被硬杀后的回收、沙箱逃逸抵抗、正式工具后端、真实模型或多Issue隔离交付验证。

## 对产品接入的影响

不新增声称可用的 `sbx` 执行后端；继续 Sandboxes 需要可用的KVM worker或上游系统修复。普通Docker容器的有限前置探针现已通过，下一增量可据此实现执行会话接口，但不能自动把现有CLI标为已支持容器，也不在沙箱失败时退回宿主执行。

产品接口目标仍是：控制面保留模型／GitHub凭证、预算、状态和审批，每个任务拥有独立执行会话。文件工具、实现自测与编排层独立验证都应进入该环境，导出结果须在停止仓库代码后校验。当前已完成普通容器前置可行性探针，尚未实现产品隔离执行。
