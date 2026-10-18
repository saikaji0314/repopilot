# Docker 执行后端验证

日期：2026-10-18。目标是把此前普通容器探针接入正式 runtime：实现、返工、独立验证、审核共用隔离接口，预检失败不回退宿主，停止后完整校验产物再导回托管 worktree。配置、源码导航及限制见 [SANDBOX](SANDBOX.md)。

## 环境与镜像

- Windows 控制端运行 RepoPilot，使用 WSL Ubuntu-24.04 内 Docker Engine/CLI 29.9.1；不是 Docker Desktop 或 Docker Sandboxes。
- Python 基础镜像：`python@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258`。
- Node 构建来源：`node@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392`。
- 使用 [Dockerfile](../../examples/runtime/docker/PythonNode.Dockerfile)离线构建，基础镜像已缓存；得到本地固定 ID `sha256:3c8ad86012a2682882bbb31eb5aa0cf2b745bd3cabadbadab3f1417d65e8d408`，Python 3.12.15、Node v22.23.3。本地构建 ID 仅标识本次制品，不承诺跨机器构建得到相同 ID。

## 验收与结果

| 需求 | 实现位置 | 本次验证 |
| --- | --- | --- |
| 各阶段统一隔离，不裸跑独立检查 | `phases.py / sandbox.py / repository.py` | Python 文件编辑与 Python 检查、JavaScript `.mjs` 编辑与 Node 检查分别通过；实现后暂停、resume 后独立验证、审核，均到达 awaiting_approval |
| 环境固定、凭证留宿主 | `config.ExecutionConfig / sandbox_process.py` | 只接受摘要或完整本地镜像 ID；三个阶段事件绑定同一镜像；合成 DS_API_KEY 诱饵不出现在容器检查环境，容器 UID65532、无 `.git` 与宿主挂载 |
| 失败不回退 | `repository_session / run_check` | Docker 不可用时 workspace_error、模型调用 0；缺失容器注入接口时拒绝工具装配；宿主检查替身确认未被调用 |
| 安全交换产物 | `sandbox_archive.py` | 路径穿越、敏感名、大小写冲突、Windows 设备名、重复路径、特殊文件、体积超限拒绝；真实容器 symlink 不导入；外部宿主改动与 Git 控制文件改动被拒绝 |
| 不信检查的单一成功退出码 | `DockerSession.finish` | 只读登记检查退出 0 但修改源码时拒绝，宿主源码保持不变；文件工具写入也被拒绝 |
| 检查有界并终止后台进程 | `DockerTransport / DockerSession.run_check` | 2 MB 输出触发限额，睡眠触发超时；每次检查后停止并重启整个容器；结束后容器和卷清理成功 |

专项测试文件为 [test_sandbox.py](../../tests/runtime/test_sandbox.py)。默认不访问 Docker；真实测试需显式配置：

```powershell
$env:REPOPILOT_TEST_DOCKER_IMAGE='python@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258'
$env:REPOPILOT_TEST_NODE_IMAGE='sha256:替换为本机构建的完整镜像ID'
$env:REPOPILOT_TEST_WSL='Ubuntu-24.04'
.venv\Scripts\python.exe -m pytest tests/runtime/test_sandbox.py -q -p no:cacheprovider --basetemp .local/tmp/sandbox-validation
```

复跑应选择新的临时目录。Linux 控制端省略 WSL 变量。服务和镜像准备属于操作者步骤；测试不会自动安装 Docker、启动服务或下载镜像。

- 全量产品及历史测试：**409 passed, 9 skipped**，126.16 秒。跳过包含未启用的 Docker 集成与平台／可选环境用例，不能算作通过。
- 显式启用真实 Docker 的专项：**27 passed**，64.12 秒，覆盖上述两类语言和失败边界。
- 凭证诱饵与阶段镜像一致性另外复核两条语言流程，使用相同核心测试，不累计成新的独立安全测试。

保留的首轮失败：文件服务把字符串而非 Path 传给 Repository，导致 docker_exec_failed；修正后复跑通过。第一次默认临时目录的旧测试受本机目录权限影响，改用专用临时目录并在允许执行环境运行；没有删掉失败或计作通过。本地证据单独保留这些结果与后续成功日志。

## 结论边界

Docker 操作、文件交换、Git worktree、提交及 SQLite 恢复均为真实执行，模型回复使用确定性 FakeModel。结果验证编排及隔离机制，**不代表真实模型修复成功率**。没有调用收费 API、重跑真实 Issue 到 PR 或新建远端 PR；生产预算账本未改动。

实现和返工使用同一 PhaseRunner 入口；本轮真实容器流程覆盖首次实现，未单独做真实模型返工实验。更完整的网络、cgroup/OOM/进程限制及后台进程行为探针见历史 [VALIDATION-09](VALIDATION-09.md)，不将旧探针数量叠加到本次测试数。

尚未完成工作卷磁盘配额、硬杀后的自动回收、跨机器恢复、宿主 Git 进一步加固和不可信租户隔离。普通容器不是 microVM；Docker Sandboxes 在本机仍不可用。后续效果验证应使用固定镜像、有限预算和真实模型重新跑一条 Issue 交付链。
