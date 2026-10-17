# 增量9：Docker Sandboxes 本机接入探针

验证日期：2026-10-17。结论：安装、Windows 虚拟化和账号认证已验证；原生 Windows 沙箱创建失败，执行隔离尚未验收。没有把失败后的检查转到宿主执行。

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

该 Issue 中有用户报告在 WSL2 内安装 Linux 版可绕过。此报告不是本机验证结果。操作者已选择继续 WSL2＋Ubuntu 路线，已启用 `VirtualMachinePlatform`，系统返回需要再次重启；Ubuntu、Linux KVM 和 Linux 版 Sandboxes 尚未验证。没有通过清空状态、反复重装、扩大网络权限或使用 nightly 来掩盖失败。

## 已验证与尚未验证

| 项目 | 结果 |
| --- | --- |
| CLI 安装、版本、安装器校验 | 通过 |
| Windows 虚拟化重启生效 | 通过 |
| daemon 启动、Docker 登录 | 通过 |
| 官方 shell 镜像准备与沙箱创建 | 失败，内部 socket 错误 |
| Python／JavaScript 命令、退出码、输出 | 未验证，前提失败 |
| 文件传输、编辑、差异导出 | 未验证 |
| 宿主／跨任务文件不可访问 | 未验证 |
| 出网拒绝、CPU／内存限制 | 未验证；仅登记配置不能当证据 |
| 超时后子进程终止、停止／恢复／移除 | 未验证 |

## 对产品接入的影响

此时不新增声称可用的 `sbx` 执行后端；现有 RepoPilot 仍以宿主权限执行登记检查。后续可选择在具备前提的 Linux／WSL2 worker 继续验证 Sandboxes，或验证普通 Docker 容器后端。两条路线均须重新通过真实执行与边界探针；不能把普通容器称为每任务 microVM，也不能在沙箱失败时自动降级为宿主执行。

产品接口目标仍是：控制面保留模型／GitHub 凭证、预算、状态和审批，每个任务拥有独立执行会话。文件工具、实现自测与编排层独立验证都应进入该环境，导出结果须在停止仓库代码后校验。当前仅完成前置可行性探针的一部分，尚未实现产品隔离执行。
