# 真实交付演示

目标是展示一条有证据的工单交付链路，而不是展示模型生成了一段看似合理的代码。建议演示 5–8 分钟，讲清楚输入、实际动作、验证、人工决定和远端产物。

## 已有真实结果

| 环节 | 可核对结果 |
| --- | --- |
| 功能需求 | [Issue #1：batches 增加 --limit](https://github.com/saikaji0314/zhixianji-jobs/issues/1) |
| 实现 | CLI 正整数校验、SQL 绑定 LIMIT、11 项新测试、README 与验证记录 |
| 独立检查 | 104 项离线测试通过；宿主另行重跑也通过 |
| 审核与返工 | 2 轮审核、1 次返工：修正文档段落归属和测试数量不一致 |
| 人工审批 | 绑定完整 SHA `e3ac2677a620a93b844eaae98ab0fa7898efeaed`，明确批准后才发布 |
| 远端交付 | [草稿 PR #2](https://github.com/saikaji0314/zhixianji-jobs/pull/2)，head 与获批 SHA 相同，未合并 |
| 自动接单 | [Issue #3：离线帮助说明](https://github.com/saikaji0314/zhixianji-jobs/issues/3) 被常驻监听自动发现并分派给独立 worker |
| 自动处理结果 | 只改两份文档，93 项离线测试通过，2 轮审核、1 次返工后停在人审；未自动发布 |

两个工单使用同一未合并默认分支基线，所以测试数量分别是新增测试后的 104 和未改测试的 93。工单 #3 不包含 PR #2 尚未合并的功能；不要把它们当作同一条已经合并的主线。

## 演示顺序

1. 打开 Issue #1，说明需求及验收，不展示私密 .env 或原始模型会话。
2. 打开 PR #2 的 Files changed，展示 CLI 校验、参数绑定查询和虚构测试。说明源码与测试由 RepoPilot 内的 DeepSeek 生成，开发 Agent 没有手工替换目标补丁。
3. 查看本机运行 `issue-1-1d3bc15b6211` 的 status，以及 verification.json、review-history.json，说明“模型自称通过”和“编排层独立检查”是不同证据。
4. 展示第一次审核的文档问题和返工后的固定提交，再看 approval 记录与 PR head，说明批准的是具体版本。
5. 查看监听器状态及 Issue #3：真实日志依次出现 issues_queued、job_dispatched、job_finished，最后 awaiting_approval，不占用 worker 等人。
6. 重启监听器或重复轮询，确认同一 Issue 没有新 run；最后展示失败记录和累计预算。

```powershell
.\.venv\Scripts\repopilot.exe status issue-1-1d3bc15b6211
.\.venv\Scripts\repopilot.exe status issue-3-62bf7ca242f6
.\.venv\Scripts\repopilot.exe watch --status
```

上述命令读取本机已有证据，不重新调用模型。原始事件、预算账本和审批记录留在被忽略的本地目录，仓库只保留必要的公开结论。

## 重新演示自动接单

先按 [监听说明](WATCH.md) 配置可信仓库和检查，再运行监听器。创建带 repopilot 标签的新 Issue 后等待最多一个正常轮询周期，观察它进入独立运行。只有明确授权时才创建真实工单、推送分支和发布 PR。

手动触发时可使用公开的执行 profile，参数与成功功能演示一致：

```powershell
.\.venv\Scripts\repopilot.exe issue ISSUE_URL --task .local/github-demo.yaml --profile examples/runtime/deepseek-delivery.yaml
```

相同 Issue 重复触发会返回已有运行，不能通过删除账本或制造新预算目录重演成功。需新尝试时显式 retry，保留旧证据；需要新的真实题目时另建清晰工单。暂停时只批准核对过的完整提交，不为演示关闭独立验证或自动自批。

## 单页报告

不想在演示时一屏屏读 JSON，可以用命令把一次运行渲染成单文件报告：

```powershell
.\.venv\Scripts\repopilot.exe report issue-1-1d3bc15b6211 --html .local\demo\report.html
```

报告按「需求来源 → 关键指标 → 阶段时间线 → 实现报告 → 候选改动与差异 → 独立复核 → 审核与返工 → 人工决定 → 远端产物」排列，其中人工决定绑定完整 SHA、远端产物链接到实际草稿 PR。它只读本机已落盘证据，不调用模型、不改变任何运行结果，本机路径与常见凭证已脱敏，可直接离线打开或截图。已渲染的真实结果见[展示记录](VALIDATION-10.md)。

## 展示边界

这是一条功能工单和一条文档工单的开发复测，已看过仓库并据失败调整过 harness；不是隐藏评测或统计成功率。之前的多次非思考模式失败、400 接线失败和费用预留均保留，详见 [验证记录](VALIDATION-05.md)。

PR #2 已公开标注一处非阻断细节：目标文档新小节标题日期为 2026-10-15，实际运行及验证为 2026-10-16，用户知情后批准。第二个候选仍需单独人审；不把它演示为已发布。

当前展示材料是可复现命令、实际 GitHub 工单／PR、单页报告与验证记录；尚未制作录屏或实时控制台。后续录屏应保留必要终端与 PR 画面，遮蔽凭证及本地私密资料；报告中的证据是历史回放，不能当作监听器在线。
