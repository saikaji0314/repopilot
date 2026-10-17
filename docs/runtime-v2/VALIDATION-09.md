# 增量9：Docker Sandboxes 本机接入探针

验证日期：2026-10-17。结论：原生 Windows 沙箱创建失败；继续安装 WSL2＋Ubuntu 后，Linux 路线又在 KVM 前提上失败。执行隔离尚未验收，没有把失败后的检查转到宿主执行。

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

## 已验证与尚未验证

| 项目 | 结果 |
| --- | --- |
| CLI 安装、版本、安装器校验 | 通过 |
| Windows 虚拟化重启生效 | 通过 |
| daemon 启动、Docker 登录 | 通过 |
| 官方 shell 镜像准备与沙箱创建 | 失败，内部 socket 错误 |
| WSL2 与 Ubuntu 24.04 安装启动 | 通过 |
| WSL2 内 KVM／嵌套虚拟化前提 | 失败，无 svm 与 /dev/kvm |
| Python／JavaScript 命令、退出码、输出 | 未验证，前提失败 |
| 文件传输、编辑、差异导出 | 未验证 |
| 宿主／跨任务文件不可访问 | 未验证 |
| 出网拒绝、CPU／内存限制 | 未验证；仅登记配置不能当证据 |
| 超时后子进程终止、停止／恢复／移除 | 未验证 |

## 对产品接入的影响

此时不新增声称可用的 `sbx` 执行后端；现有 RepoPilot 仍以宿主权限执行登记检查。继续 Sandboxes 需要可用的 KVM Linux worker 或系统上游修复；本机已装的 Ubuntu 本身还不足以满足前提。另一个候选是在现有 Ubuntu 内验证 Docker Engine 普通容器，此路线不需要在 WSL 内再运行 KVM 虚拟机，但隔离边界不同，尚未安装或验证。两条路线均须通过真实执行与边界探针；不能把普通容器称为每任务 microVM，也不能在沙箱失败时自动降级为宿主执行。

产品接口目标仍是：控制面保留模型／GitHub 凭证、预算、状态和审批，每个任务拥有独立执行会话。文件工具、实现自测与编排层独立验证都应进入该环境，导出结果须在停止仓库代码后校验。当前仅完成前置可行性探针的一部分，尚未实现产品隔离执行。
