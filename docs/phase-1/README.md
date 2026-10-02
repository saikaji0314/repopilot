# 第 1 期：最小单 Agent MVP

> 当前状态：阶段性收尾；最新批次严格通过 4/6，用户决定进入第二期  
> 核心目标：亲手实现并理解“模型决策—工具执行—观察回传—继续或结束”的最小闭环

最新进展：六任务合法答案 4/6、严格通过 4/6，达到原数量门槛。见 [收尾报告](../../evals/reports/agent-v2.md)；[首轮报告](../../evals/reports/agent-v1.md) 保留。最近单元测试记录为 首轮评测 的 143 passed、1 skipped，不是本次重跑结果。剩余限制继续见 BACKLOG，不因阶段收尾注销。现在开始 [第二期](../phase-2/README.md)。以下原定范围与验收标准保留。

## 1. 本期要证明什么

第 0 期证明了单次 LLM 可以看懂完整仓库快照：12/12 次找到正确根因，但严格通过只有 8/12，主要缺口是验证没有闭环。

第 1 期不追求更多功能，而是回答：

> 当模型不再一次性看到完整仓库，只能通过受控工具主动调查时，能否收集真实证据、完成诊断，并在异常或资源耗尽时安全停止？

本期完成后，RepoPilot 应该是一个可从命令行运行的只读 Agent，而不是一次模型问答脚本。

## 2. 本期范围

### 必须完成

- CLI：按任务 ID 启动一条 Agent 运行；
- 原生 Tool Calling：使用 OpenAI 兼容 `tools` / `tool_calls` 协议；
- 四个只读工具：`list_files`、`search_code`、`read_file`、`run_test`；
- Agent Loop：处理模型回复、工具调用、观察结果和最终答案；
- 限制：最大模型轮数、最大工具次数、工具超时、整任务超时、Token 和费用上限；
- 轨迹：保存消息、工具调用、工具结果、耗时、Token、费用和停止原因；
- 错误处理：未知工具、参数错误、工具异常、模型异常、最终格式错误和达到限制；
- 自动化测试：工具安全边界、循环状态转换和主要异常路径；
- 评测：在第 0 期的 6 个任务上各运行 1 次，并与单次 LLM 基线比较。

### 明确不做

- 不修改文件、不应用模型生成的补丁；
- 不提供任意 Shell 或任意命令执行；
- 不实现 FastAPI、SQLite 持久化或断点恢复；
- 不加入 LangChain、LangGraph 或多 Agent；
- 不做 AST、向量检索或索引；
- 不把完整仓库快照一次性放入 Prompt；
- 不重写第 0 期基线代码来追求统一抽象。

这些能力分别属于后续阶段。第 1 期的代码可以为后续留出接口，但不能提前实现。

## 3. 用户运行方式

计划中的 CLI：

```powershell
# 先用 Fake 模型和本地工具验证循环，不产生 API 费用
.\.venv\Scripts\python.exe -m pytest -q

# 第一条真实 Agent 任务
.\.venv\Scripts\python.exe -m scripts.run_agent `
  --task flaskr-auth-001

# 本期通过后才运行 6 条评测
.\.venv\Scripts\python.exe -m scripts.run_agent --all
```

安装为可编辑包后，最终也应支持：

```powershell
run-agent --task flaskr-auth-001
```

本期不提供自由选择任意仓库的 CLI 参数。运行器只从 `evals/tasks/` 选择任务，并为每次运行创建注入单一缺陷的临时工作区，以保证评测可重复。

## 4. 计划目录

```text
repopilot/
├── agent/
│   ├── loop.py              # Agent Loop 与停止条件
│   ├── prompts.py           # Agent 专用 Prompt，不复用基线 Prompt
│   ├── schemas.py           # 状态、轨迹事件和最终答案协议
│   └── trace.py             # JSON/JSONL 轨迹保存
├── tools/
│   ├── base.py              # 工具协议、统一结果和注册表
│   ├── filesystem.py        # list_files、read_file
│   ├── search.py            # search_code
│   ├── testing.py           # 白名单 pytest
│   └── path_guard.py        # 工作区路径安全检查
├── agent_runner.py          # 任务工作区、模型、工具和 Agent 的编排
├── llm_client.py            # 扩展为支持多轮 messages 与 tools
└── config.py                # 增加 AgentSettings，保留 BaselineSettings

scripts/
└── run_agent.py             # 薄 CLI 包装

configs/
└── agent.yaml               # 第 1 期模型与资源限制

tests/
├── agent/
└── tools/
```

## 5. 与第 0 期代码的关系

| 现有模块 | 第 1 期处理方式 | 原因 |
| --- | --- | --- |
| `workspace.py` | 直接复用 | 已能创建、变异、清理隔离工作区 |
| `task_loader.py` | 保留解析；新增 Agent Prompt 构造函数 | 现有 Prompt 明确写着“无工具、给完整快照”，不能复用 |
| `schemas.py` | 复用 `BaselineAnswer` 作为最终答案；Agent 状态另放新模块 | 保持评测输出可比，同时避免基线协议被循环状态污染 |
| `verification.py` | 复用 `CommandResult`；不直接把 `run_command` 暴露给模型 | Agent 只能通过更严格的 `run_test` 白名单调用 pytest |
| `llm_client.py` | 小步扩展 | 需要接收多轮 messages、tools，并返回 tool_calls |
| `baseline.py` | 不改行为 | 保留第 0 期对照组的可复现性 |
| `repository_snapshot.py` | Agent 运行不使用 | 第 1 期必须让模型主动检索，而非获得完整快照 |

## 6. 验收标准

只有同时满足以下条件，第 1 期才完成：

- 6 个初始任务中至少 4 个严格通过 Oracle；
- 每个成功答案至少引用一条由工具实际获得的代码或测试证据；
- 不发生工作区外读取、任意命令执行或文件修改；
- 模型格式错误、工具错误、最大步数和总超时都能形成明确停止原因；
- Fake 模型单元测试不调用真实 API，并覆盖成功循环与受控失败循环；
- 保存的轨迹足以回答“模型看到什么、调用了什么、工具返回什么、为何停止”；
- 生成一份与第 0 期相同口径的质量、步骤、耗时、Token 和费用报告；
- 至少记录一个成功案例和一个失败案例；
- 项目作者能够脱离代码画出 Agent Loop，并解释每个安全边界。

## 7. 本期文档

- [Agent Loop 设计](./AGENT_LOOP_DESIGN.md)
- [工具安全契约](./TOOL_CONTRACTS.md)
- [实施与学习计划](./IMPLEMENTATION_PLAN.md)
- [待补缺清单](./BACKLOG.md)
- [步骤 6：Trace 实现指导](./TRACE_IMPLEMENTATION_GUIDE.md)
- [步骤 7、8：适配器与运行器指导](./MODEL_RUNNER_GUIDE.md)
- [评测准备记录](./EVALUATION_READINESS.md)

## 8. 审核时重点确认

1. 是否接受第 1 期只支持评测任务模式，不支持任意仓库和自由题面；
2. 是否接受使用原生 Tool Calling，不自定义 ReAct 文本协议；
3. 是否接受 `run_test` 只运行 `tests/` 下的 pytest 目标，不提供 Shell；
4. 是否接受先用 Fake 模型把循环测试完整，再进行一次真实 API 烟雾测试；
5. 是否接受严格通过至少 4/6 作为本期门槛，而不是要求立刻超过单次 LLM 的 8/12 基线比例。
