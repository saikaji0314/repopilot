# 评测准备记录

> 新批次严格通过 4/6，用户决定阶段性收尾并进入第二期。见 [最新报告](../../evals/reports/agent-v2.md)。本页其余内容为历史准备记录。

> 后续更新：本页以下保留为评测前记录。现已完成六任务首轮试跑，本地测试为 143 passed、1 skipped；合法答案 5/6、严格通过 3/6，尚未达到 4/6 门槛。Agent 请求现已传入 JSON 输出配置并增加有限格式恢复，不再沿用下文历史请求差异。当前结论见 [验收报告](../../evals/reports/agent-v1.md)，后续安排见 [路线](../ROADMAP.md)。

结论：可以开始固定 Flaskr 任务上的单任务真实试跑；真实协议验证通过后再开展六任务评测。

## 已验证

- 项目 `.venv` 全量测试：120 passed，1 skipped（符号链接创建权限限制）。
- 基线 dry-run：干净仓库 24 个测试通过，六条任务缺陷注入及失败证据预检全部通过。
- CLI 帮助入口正常。
- 用 Fake 模型串联真实运行器、临时工作区和工具：list_files → read_file → run_test → 假最终答案，4 轮、3 次工具调用成功。
- 真实 pytest 对 auth 缺陷返回 1，工具仍 ok=true；五种产物均可生成。该检查使用临时输出目录，完成后已清理，没有模型调用费用，也不属于诊断评分。

未验证：当前账号/模型的真实 Tool Calling、thinking 与工具协议兼容性、多轮延迟和诊断质量。本次没有进行真实 API 请求。

## 实际执行顺序

先做最小 list_files 真实烟雾测试，或用下面单任务试跑兼作首次端到端冒烟检查：

```powershell
.\.venv\Scripts\python.exe -m scripts.run_agent --task flaskr-auth-001 --repetitions 1
```

查看 `evals/runs/agent/<batch-id>/flaskr-auth-001/run-01/`：

1. trace 至少有一次真实工具调用及结果；若直接给答案，不能据此认定工具协议已验证。
2. assistant/tool 消息的 call ID 配对，观察内容可信。
3. summary 的终态、停止原因、用量和费用；费用是配置单价估算。
4. 最终答案证据能在轨迹中追溯，未声称执行过不存在的操作。
5. 运行失败时区分接口/环境错误、达到限制和模型诊断失败。

通过后固定代码、配置、模型、任务版本，再运行：

```powershell
.\.venv\Scripts\python.exe -m scripts.run_agent --all --repetitions 1
```

必须显式传 repetitions=1；当前 YAML 默认 2 次，直接 --all 会尝试 12 次。
试跑及调参记录单独保留；正式六任务结果形成独立批次。

## 评分与遗漏

`completed` 只表示返回了符合格式的答案，不等于诊断正确；`insufficient_evidence` 也可能通过格式验证。

每条任务依据自身 oracle.yaml：记录 rubric 得分、每个 hard_requirement 是否满足、严格通过与否；达到该题分数线且全部硬条件满足才算严格通过。Oracle 只用于评判，不能加入 Agent 上下文。

先人工评分即可，建议记录：task_id、run_dir、运行终态、总分、硬条件失败项、严格通过、轮数、工具次数、Token、估算费用、耗时、简短原因。
六条都纳入统计；基础设施失败也保留，不要只挑成功答案。当前缺少批次总报告，可先手动汇总各 summary。

与第 0 期比较时说明样本数：基线 12 次，Agent 本批 6 次；披露输入方式、调用次数及 JSON 输出模式差异，不把一次小样本结果解释为稳定胜出。

小型固定任务可以先试跑。正式报告前需处理或明确披露 BACKLOG 中的路径防护、输出上限和总超时等验收缺口；不能把单元测试通过表述为全部安全边界通过。
