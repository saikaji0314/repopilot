# Agent 执行引擎

执行引擎将单次问答扩展为“模型决策 → 工具执行 → 观察回传”的多轮流程。模型通过工具主动获取证据，宿主代码管理权限、资源限制和结束条件，输出可校验的诊断与执行轨迹。

## 消息循环与模块边界

`BailianChatModel` 将 OpenAI 兼容响应转换为统一 `ModelTurn`，保留文本、工具调用和用量。Loop 读取工具调用，由注册表校验参数并执行，将结果按调用 ID 写回消息历史。模型生成最终文本时，用 Pydantic 校验诊断协议，再形成运行终态。

| 模块 | 职责 |
| --- | --- |
| [agent/schemas.py](../../repopilot/agent/schemas.py) | 模型轮次、运行状态、停止原因与接口协议 |
| [agent/bailian.py](../../repopilot/agent/bailian.py) | 模型 SDK 适配 |
| [agent/loop.py](../../repopilot/agent/loop.py) | 多轮消息、工具观察、恢复与停止 |
| [tools/base.py](../../repopilot/tools/base.py) | 工具协议、注册与派发 |
| [tools/arguments.py](../../repopilot/tools/arguments.py) | 工具参数模型 |
| [agent_runner.py](../../repopilot/agent_runner.py) | 任务环境准备与能力装配 |

模型接口与循环分离，单元测试可以使用 Fake 模型构造工具调用、非法答案和异常响应，无需真实 API。

## 工具与执行边界

基础工具提供目录浏览、文件读取、字面搜索和指定 pytest 测试；可选符号检索通过同一注册表接入。文件访问限定于准备好的任务工作副本，测试入口限制可调用目标，模型不能选择任意解释器或 Shell 命令。

任务来自固定评测目录，运行器验证基线、注入缺陷后再进入循环。不同被测仓库由宿主选择测试环境，权限与工具说明随实际能力同步。执行过程保留当前已观察事实与修复后待验证方案的区别。

这些限制针对可信样例提供受控执行边界；任意不可信仓库的测试执行还需要更完整的进程隔离。详细契约见 [工具设计](TOOL_CONTRACTS.md)。

## 预算与错误恢复

配置支持模型轮数、工具调用次数、累计 Token、估算费用、工具超时和整体时限。达到停止条件时记录明确原因，便于区分工具错误、模型错误与资源耗尽。

允许恢复的工具参数错误按“工具＋错误类型”各提供一次纠错轮次：测试目标附带额外参数，或搜索目录误传为文件。宿主将错误回传模型，由模型重新选择参数。最终答案未通过 Schema 时，提供一次禁用工具的格式恢复请求。纠错和格式恢复都计入原运行预算。

相关行为由 [Loop 测试](../../tests/agent/test_loop.py)、[模型适配测试](../../tests/agent/test_bailian.py) 和 [工具测试](../../tests/tools) 验证。路径相关测试受本机系统条件影响，跳过项应单独查看。

## 轨迹与诊断

`messages.json` 保存模型实际看到的消息；`trace.jsonl` 保存轮次与工具事件；`summary.json` 保存终态、用量与停止原因；`final_answer.json` 保存校验后的诊断。运行器补充可见任务与实际配置快照，方便复盘模型行为。

结构合法、根因正确和严格诊断通过分别核对。六题归档批次严格通过 4/6；其中失败包括工具额度耗尽与最终答案格式错误。结果和证据见 [六题评测报告](../../evals/reports/agent-v2.md)。

## 运行与进一步阅读

```powershell
.\.venv\Scripts\python.exe -m scripts.run_agent --task flaskr-auth-001 --repetitions 1
```

环境准备见 [项目首页](../../README.md)。检索接入与方案比较见 [代码检索说明](../phase-2/README.md)。设计过程与已知问题保留在 [Loop 设计记录](AGENT_LOOP_DESIGN.md)、[实现记录](IMPLEMENTATION_PLAN.md)、[Trace 设计记录](TRACE_IMPLEMENTATION_GUIDE.md) 和 [限制清单](BACKLOG.md)；实现记录中的早期接口以当前代码为准。
