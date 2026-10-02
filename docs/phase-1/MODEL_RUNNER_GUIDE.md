# 步骤 7、8：适配器与运行器实现指导

本次新增契约测试和指导，未修改生产实现；测试默认无网络、无模型费用。

## 1. 先完成步骤 7

当前 `bailian.py` 有两个直接阻塞点：`settings.timeout` 应为 `timeout_seconds`；整个 SDK response 不能直接交给 `ModelTurn.model_validate`，需要逐字段转换。

| SDK 响应位置 | 本地协议字段 |
| --- | --- |
| choices[0].message.content | ModelTurn.content（允许 None） |
| choices[0].message.tool_calls 或空列表 | ModelTurn.tool_calls |
| call.id / call.function.name / call.function.arguments | ToolCall.id / name / arguments_json |
| choices[0].finish_reason | ModelTurn.finish_reason |
| response.usage 的三个 token 字段 | Usage |

标准字段依据：[OpenAI Function Calling 文档](https://developers.openai.com/api/docs/guides/function-calling)、[Chat Completions Python 参考](https://developers.openai.com/api/reference/python/resources/chat/subresources/completions/methods/create)。这是兼容协议参考，不证明百炼当前模型支持所有 OpenAI 参数。

`arguments_json` 保持原始字符串；注册表负责解析和校验。适配器不执行工具、不校验最终 BaselineAnswer，也不拼装 system/user Prompt。

每轮 `complete(messages, tools)` 都使用调用方给出的完整消息和工具定义；本期非流式。保留现有项目的 `thinking_enabled` 请求配置，与基线保持一致。JSON 输出模式、thinking 与工具调用的具体兼容性留给一次真实烟雾测试确认，不凭 OpenAI 文档推断百炼支持。

本次测试固定的异常策略：空 choices、usage 缺失时抛明确 ValueError；SDK 请求异常传给 Loop 处理。缺 usage 代表用量未知，本项目预算限制场景暂不记为零。

测试通过只能证明请求构造和响应转换，不能证明真实鉴权、模型兼容或诊断质量。

## 2. 再完成步骤 8

继续沿用当前函数，先规定 `run_agent(...) -> int`：全部成功返回 0，有运行或预检失败返回 1；非法任务 ID、非正 repetitions 抛 ValueError。CLI 捕获用户参数错误并用 argparse 报错。

当前必须修改：

1. `_select_task_dirs(settings, task_ids)` 使用函数入参，配置没有 task_ids。
2. `repetitions is None` 才取默认值；`0 or default` 会吞掉非法的 0。
3. 根据 `result.status == AgentStatus.COMPLETED` 判定成功，AgentResult 没有 success 属性。
4. 为任务和重复次数建立双层循环，将最终 return 移到循环外；任何一条失败都使最终退出码非 0。
5. 每次调用 Loop 分配独立 run_id/输出目录。当前所有任务共用 run_id 会覆盖 messages/summary，并混合 trace.jsonl。
6. 补 `verify_task_defect`：确认缺陷注入后的实际失败证据，再允许调用 Loop。
7. 用现有 `with prepared_task_workspace(...)` 保证清理；捕获预检/Loop 意外异常并形成失败结果。初始化失败也应向 CLI 明确报错。
8. 每次 Loop 前保存 `task.json`。`parse_task` 加入了内部 `_task_dir: Path`，不能直接将整个 dict 写成 JSON；选取 Agent 可见字段，排除内部字段、Oracle、补丁内容、配置密钥。

建议结果目录（不强制测试绑定这一层级，但每次 Loop 必须独立）：

```text
evals/runs/agent/<batch-id>/<task-id>/run-01/
    task.json
    messages.json
    trace.jsonl
    summary.json
    final_answer.json   # 仅成功
```

接口更新：Loop 直接接收完整的 `run_dir: Path | None`，不再拼接 `settings.runs_dir / run_id`。runner 在同一个 run_dir 保存 task.json，并调用 `agent_loop(..., run_dir=run_dir, task_id=task["id"])`。task_id 是独立的题目编号，不从目录名推断；省略时事件中为 null。run_dir=None 关闭保存。目录由运行器构造，题目 ID 应先校验再用于路径。

保持任务副本和轨迹在临时仓库外。预检失败时 Loop 未运行，由运行器保存失败说明；不要伪造模型轮次或 Token。

## 3. CLI 保持薄包装

建议在 `agent_runner.py` 加 `build_parser()` 和 `main(argv=None)`：

- `--task` 可重复，或 `--all`，两者互斥且必须选一个。
- `--repetitions` 可选。
- `main` 解析后调用 run_agent，最后 `raise SystemExit(exit_code)`。
- `scripts/run_agent.py` 只导入并调用 main。只打印退出码不会改变操作系统收到的退出码。

先实现以上入口；是否额外添加 pyproject 的 `run-agent` 命令可以后续处理。

## 4. 测试如何隔离

- `test_bailian.py`：替换模块中的 OpenAI 构造函数，返回具有相同属性结构的假 SDK 客户端。
- `test_agent_runner.py`：替换配置加载、预检、模型、临时仓库和 Loop，检查编排参数、task 快照、批量次数、独立目录和清理。测试用临时目录，不运行真实 git/pytest 子进程。
- `test_agent_cli.py`：替换 run_agent，检查参数与 SystemExit；用 runpy 验证实际脚本入口。

测试规定 main(argv) 是新增接口，当前不存在时以断言失败提示，不会导致整个测试集收集失败。

```powershell
.\.venv\Scripts\python.exe -m pytest tests/agent/test_bailian.py -q -x
.\.venv\Scripts\python.exe -m pytest tests/agent/test_agent_runner.py -q -x
.\.venv\Scripts\python.exe -m pytest tests/agent/test_agent_cli.py -q -x
.\.venv\Scripts\python.exe -m pytest -q
```

## 5. 通过后如何进入真实运行

先修清单中的脱敏调用错误和相关边界，再手动做一次最小烟雾测试：在已知临时仓库请求 list_files，确认标准 tool_calls、tool 消息和最终输出完整。之后只跑一个任务一次，检查产物，再考虑全部任务。当前测试不触发这类付费运行。

暂不在这轮扩大为完整评测系统。批量总报告、预检异常产物的详细协议、混合成功失败的批次、客户端关闭和模型截断响应等测试后续补上。

理解检查：SDK 响应和本地协议为何需要映射？为什么 task 快照和临时仓库属于运行器？为什么同一任务重复两次需要两个结果目录？为什么函数 return 1 和进程退出码 1 不同？
