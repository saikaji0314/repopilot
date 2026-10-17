# RepoPilot · 通用仓库研发 Agent

RepoPilot 面向由工单驱动的仓库研发：手动接收或自动监听已登记仓库的新 GitHub Issue，在独立 Git worktree 中实现，独立重跑检查，经只读审核 Agent 与有限返工后暂停，人工批准具体提交，再发布草稿 PR。模型内层循环使用 LangChain／LangGraph，当前接入 DeepSeek。

核心不依赖 Python AST 或 pytest；UTF-8 文件工具与操作者登记的命令支持不同语言仓库。已验证范围及实际运行结果见[真实交付验证](docs/runtime-v2/VALIDATION-05.md)，完整协议见[运行时说明](docs/runtime-v2/README.md)，可按[演示步骤](docs/runtime-v2/DEMO.md)查看真实工单到草稿 PR 的证据。历史 Python 诊断入口和题集仍保留，其结果不代表新运行时的修复效果。

整体进度、与原始愿景的差距、技术概念及代码阅读顺序见[架构与现状](docs/runtime-v2/ARCHITECTURE.md)。

记忆、OS隔离、独立评测、展示和合并发布的候选方案及阶段验收见[后续设计](docs/runtime-v2/NEXT.md)，已完成部分与待实施内容分别标注。

新增[两题独立修复评分](evals/repair/README.md)：不调用模型，从固定基线应用候选补丁并运行工作区外验收；目前仅用于公开开发夹具验证评分机制，不代表模型修复成绩。

可选[仓库事实记忆](docs/runtime-v2/MEMORY.md)记录带提交与文件指纹的源码书签，供实现、审核和返工定位；新运行用 `--memory` 开启，默认关闭。自动候选需要人工确认，尚无真实模型收益结论。

[Docker Sandboxes 接入探针](docs/runtime-v2/VALIDATION-09.md)中，原生 Windows 后端在镜像准备阶段失败；WSL2＋Ubuntu 安装成功，但 KVM 前提失败。尚未提供产品隔离执行能力。

## 安装与配置

准备 Python 3.12 和 Git；JavaScript 示例另需 Node.js。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -c constraints.txt -e ".[test]"
# 已有 .env 时保留内容
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

在 `.env` 中填写 `DS_API_KEY`；默认 `DS_BASE_URL=https://api.deepseek.com`、`DS_MODEL=deepseek-flash`、`DS_THINKING=enabled`、`DS_REASONING_EFFORT=low`。旧运行保持冻结配置；新交付演示使用思考模式，SDK 接线保留其工具调用所需字段，但不记录或展示原始思考文本。GitHub 功能另需 `GITHUB_TOKEN` 或 `GH_TOKEN`，权限按目标仓库配置：读取仓库及 Issue、发布分支需要 Contents 写入、创建 PR 需要 Pull requests 写入；只有创建演示 Issue 时才需 Issues 写入。进程环境变量优先。

## 运行与审批

以下运行命令调用真实模型并产生费用。任务 YAML 声明可信仓库路径、需求和命名检查；路径相对 YAML，命令是参数数组，`{python}` 使用当前解释器。Issue 只提供需求，不能修改本地登记的命令或仓库路径。

```powershell
# 本地任务；兼容不写 run 的旧命令
.\.venv\Scripts\repopilot.exe run examples/runtime/javascript-implement.yaml
# GitHub Issue；--task 指向你本机接入的仓库和检查模板
.\.venv\Scripts\repopilot.exe issue https://github.com/OWNER/REPO/issues/1 --task task.yaml
# 在阶段完成后主动暂停，另一个进程继续
.\.venv\Scripts\repopilot.exe run task.yaml --stop-after implement
.\.venv\Scripts\repopilot.exe jobs
.\.venv\Scripts\repopilot.exe status RUN_ID
# 把这次运行的证据渲染成单文件 HTML，可直接离线打开或截图
.\.venv\Scripts\repopilot.exe report RUN_ID --html demo.html
.\.venv\Scripts\repopilot.exe resume RUN_ID
# 查看差异、检查证据和审核意见后，批准完整 SHA；批准本身不推送
.\.venv\Scripts\repopilot.exe approve RUN_ID --commit FULL_SHA --reason "已核对差异与检查"
.\.venv\Scripts\repopilot.exe publish RUN_ID
# 也可以拒绝
.\.venv\Scripts\repopilot.exe reject RUN_ID --commit FULL_SHA --reason "需求尚未满足"
```

默认启用工作区和审核，审核通过后的状态是 `awaiting_approval`。只有批准绑定版本才能发布 GitHub 草稿 PR；不会自动合并或发布软件。同一 Issue 重复接入返回已有运行；Issue 内容变化、原始仓库远端不符、审批后版本变化均拒绝发布。失败后可显式 `retry RUN_ID --reason "原因"` 新开一次尝试，旧运行完整保留；同一 Issue 的普通接入始终指向最新尝试。Issue 内容变化不能靠 retry 静默覆盖。

`--no-review` 只做实现；`--no-workspace` 直接只读原目录、禁用编辑，适合无 Git 的快照。这两种运行不能进入 PR 交付。`--max-rework N` 限定返工次数（默认 3，上限 5）。`completed` 仅表示报告流程完成，需同时核对报告结论，不能当作已修复或已交付。

进程在阶段内部退出时，`resume` 会返回 `recovery_required`，不会盲目重放模型或检查。核对现场后可用 `recover RUN_ID --commit FULL_SHA --reason "现场核对结论"` 接纳当前工作树，再 `resume` 从该阶段重新开始。恢复不逐条重建模型对话；已完成阶段无需重新调用模型，累计用量、编辑次数、返工次数和预算保留。发布中断则直接重试 `publish`，先核对远端副作用。

## 自动监听与真实演示

`repopilot report RUN_ID` 把一次运行的已有证据渲染成**单文件、可离线打开**的 HTML 报告：需求来源、关键指标、阶段时间线、实现报告、候选改动与差异、编排层独立复核、审核与返工、人工决定（绑定提交）与远端 PR。它只读已落盘证据，不调模型、不改结果、不提供写操作；本机绝对路径与常见凭证会被脱敏，内容经 HTML 转义。适合作为展示与复盘材料，详见[展示记录](docs/runtime-v2/VALIDATION-10.md)。

已有真实结果：[zhixianji-jobs Issue #1](https://github.com/saikaji0314/zhixianji-jobs/issues/1) 经实现、104 项独立检查、两轮审核和一次返工，再由用户批准具体提交后创建[草稿 PR #2](https://github.com/saikaji0314/zhixianji-jobs/pull/2)，未合并。[Issue #3](https://github.com/saikaji0314/zhixianji-jobs/issues/3) 则由常驻监听自动接单并完成，93 项检查通过，停在人工审批。

```powershell
# 先复制并修改 watch.example.yaml，登记可信本机仓库、检查和接单标签
.\.venv\Scripts\repopilot.exe watch --config .local/watch.yaml
.\.venv\Scripts\repopilot.exe watch --status
.\.venv\Scripts\repopilot.exe watch --stop
# 手动接入可使用与成功功能演示一致的执行参数
.\.venv\Scripts\repopilot.exe issue ISSUE_URL --task task.yaml --profile examples/runtime/deepseek-delivery.yaml
```

监听器按仓库和标签准入，SQLite 去重后交给有上限的 worker 进程池，每个任务独立 worktree。默认每 60 秒轮询、一个 worker、同仓库一次一个任务；审批等待不占 worker，失败不自动无限重试。停止后等待当前任务落盘；没有安装系统服务或开机自启。参数与恢复边界见[监听说明](docs/runtime-v2/WATCH.md)。

这两条工单是开发复测，不能代表通用成功率。前期非思考模式的过度调查、接线失败与费用记录均保留；当前仍需人审，模型审核可能遗漏问题。[验证记录](docs/runtime-v2/VALIDATION-05.md)区分真实 GitHub／模型结果和模拟测试。

## 证据与限制

运行数据保存在被忽略的 `.local/runtime/`：`jobs.sqlite3` 是任务状态与审批记录，`budget.sqlite3` 是既有累计预算账本，`locks/` 防止同一任务并行写入，`worktrees/<id>/` 保留工作区，`runs/<id>/` 保存本地证据。

| 产物 | 内容 |
| --- | --- |
| `input.json` / `events.jsonl` | 冻结输入、配置版本与模型／工具事件 |
| `report.json` / `changes.json` | 实现结论、相对基线的差异与文件统计 |
| `verification.json` | 编排层独立执行全部登记检查的真实退出码与输出尾部 |
| `review.json` / `review-history.json` | 只读审核结论、逐轮提交与 findings |
| `summary.json` | 最近一次运行或恢复的摘要、用量与原仓库内容是否变化；发布后的最新状态以 `status` 为准 |

审核对象绑定提交。独立检查失败、超时或缺少登记检查时，即使模型批准也不能通过；检查及审核前后工作树必须保持该版本干净。人工批准绑定需求、Issue、提交、审核及验证结果的摘要；发布前再次核对。工作区提交使用单次命令的 Git 身份，不改写原仓库共享配置。

上下文增长时，框架按字节阈值清理较早的读取结果并保留近期内容、工具配对、编辑和检查证据；完整输出仍留在事件中。这不是语义总结，可能需要重新读取文件。

默认每次运行最多 36 次模型请求、40 次工具调用、12 次编辑、3 次返工、300 秒活跃执行时间，估算费用上限 2 元；累计窗口默认 200 元。可在启动或显式 retry 时用 `--profile` 或 `--max-context-bytes`、`--max-tool-calls`、`--max-run-cost-cny` 调整限额，resume 保持原配置。公开的交付 profile 为 48 次模型、80 次工具、24 次编辑、8192 输出 tokens、900 秒和单次 8 元；现有累计账本仍为硬边界。不清零既有账本；失败或用量不明仍保留费用预留。暂停等待不消耗活跃执行期限。费用是保守估算，实付以供应商账单为准。

只用于预先接入的可信仓库。本机检查以当前用户权限运行；路径限制、worktree 和环境变量清理不提供操作系统沙箱。工具拒绝 `.env*`、`.git`、`.local`、数据目录及链接等路径，但宿主进程和仓库代码仍有本机权限。远程 tracing 默认关闭；原始输入、差异及日志只留本机，不随 Git 分发。发布使用 GitHub REST 和 Git，不要求安装 `gh`。

## 历史实现

旧版 Python 只读诊断、AST 检索与十题评测保留原路径，说明集中在[历史实现文档](docs/legacy/README.md)。新版业务代码不调用旧版模块，但两版仍共用安装包；边界与保留策略见[架构与现状](docs/runtime-v2/ARCHITECTURE.md)。

## 扩展方向

自动合并、软件发布、操作系统沙箱、跨机器恢复和长期部署仍是后续范围。首版支持人工触发与常驻监听、受控本机执行和审批后草稿 PR。历史诊断通过率与新运行时实现效果分别记录。

Flask 源码与教程样例来自 Pallets，保留其上游 README 和 BSD-3-Clause 许可证。
