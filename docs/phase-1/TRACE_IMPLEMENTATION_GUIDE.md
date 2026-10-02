# 步骤 6：Trace 检查与实现指导

接口更新：下文是步骤 6 开发时的历史检查。当前 Loop 使用 `run_dir: Path | None` 指定完整输出目录，`task_id` 单独传入；原 run_id 调用示例已过时。run_dir=None 表示不落盘。

新增测试是目标契约；本次没有代写 Trace 或循环实现。

## 1. task.json 放在哪里

放在 `agent_runner.py` 的准备阶段是合适的，步骤 8 接 CLI/运行器时再实现。
现在先固定责任：运行器生成唯一 run_id、建立持久运行目录、解析并保存 Agent 可见任务，再准备临时仓库、构造 messages、调用循环。

`task.json` 只存本次 Agent 可见的任务字段和初始证据，不包含 Oracle、补丁内容、完整配置或密钥。运行目录位于临时仓库之外。

`task_id` 是题目编号，`run_id` 是某次运行编号；同一道题运行两次应有两个 run_id。
当前 `run_started` 把 run_id 写进 task_id，应改用 run_id；真正的 task_id 以后由运行器传入。

| 文件 | 写入责任与时机 |
| --- | --- |
| task.json | 运行器；任务加载后、循环开始前 |
| messages.json | Trace 保存完整数组；循环结束时必须更新最终版本 |
| trace.jsonl | 每发生一次事件，追加一行 JSON |
| summary.json | `_finish` 汇总所有终态；成功、失败、达到限制都保存 |
| final_answer.json | 仅成功完成时保存 |

## 2. 当前必须先修的 Trace 问题

1. `save_trace` 循环调用 `write_text`，消息列表最终只剩最后一项；空列表只会生成空文件。应先把整个对象转成 JSON，再一次写入。
2. `save_trace_event` 传入 dict，但 `save_trace` 没有 dict 分支，首个事件就会抛错。
3. JSONL 必须追加，每行一个完整 JSON 对象；不能复用覆盖写入的快照逻辑。JSONL 不使用多行缩进。
4. 明确 `encoding="utf-8"`；`ensure_ascii=False` 可让中文直接可读。
5. `run_id=None` 时，`run_root / 文件名` 会在进入保存函数前报错。保存函数里检查 None 已来不及，需要在路径拼接之前处理禁用状态。
6. `_finish` 使用了不存在的 `TraceEvent.RUN_COMPLETED`，已有枚举是 `RUN_FINISHED`。
7. `_finish` 必须保存最终 messages。当前只在下一轮开始时保存，最终回答或失败工具结果会缺失。
8. 费用、耗时、error 和 final_answer_path 现在只加入结束事件，应写进 summary；失败时 final_answer_path 为 null。
9. 模型响应事件的 model_step 当前从 0 开始，而工具事件从 1 开始；统一 1-based。收到响应即累计步数和 usage，超时/上限停止也不能漏掉已发生的消耗。
10. 工具抛异常后也应生成失败 ToolResult，保存 tool_call_completed 和 tool 消息，再进入统一 `_finish`。
11. 不保存 settings 整体；异常文本也可能带入已知 API Key，落盘前统一脱敏。

## 3. 怎样整理得更清楚

已经抽出 `_finish` 是合适的：所有终态都走同一个收尾入口。保留这个结构即可。

先把 `trace.py` 分清两种操作：

```python
save_trace(path, value)               # JSON 快照：覆盖、允许缩进
save_trace_event(path, event, data)   # JSONL 事件：追加、一行一个对象
```

它们共用一个小型 JSON 转换函数：遇到 BaseModel 用 `model_dump(mode="json")`，遇到 list/dict 递归转换，其他 JSON 基本类型原样返回。然后整体调用 `json.dumps`。不要在列表循环中写文件。

文件可直接用 `write_text` 或 `open` 创建，无需先 `touch`；父目录使用 `mkdir(parents=True, exist_ok=True)`。

函数测试通过后，如循环仍充斥路径拼接，可以加一个小 `TraceRecorder`，持有可选 run_root，提供 `snapshot(name, value)` 和 `event(kind, data)` 两个方法。run_root=None 时方法直接返回，循环就不用每处判断。现有保存函数仍可作为底层函数保留。

建议保持三层职责：

- 运行器：题目、运行目录、临时仓库和依赖组装。
- 循环：模型/工具调用、消息历史、计数及停止原因。
- Trace：序列化、文件写入、时间戳和脱敏。

本阶段可以继续沿用当前 `run_id` 接口，等步骤 8 再把 Recorder 的构造移到运行器；不必同时改所有接口。

`_finish` 中只组装一次 summary，再用同一份状态和指标生成结束事件，避免两处字段逐渐不一致。可以保留 AgentResult 原有字段，不必为写文件强行改变循环返回协议。

## 4. 新增测试怎么读

`tests/agent/test_trace.py`：先验证底层写入，覆盖完整/空列表、字典快照覆盖、Pydantic 列表、JSONL 追加、嵌套模型、禁用保存。

`tests/agent/test_loop_trace.py`：验证循环与文件的集成，覆盖成功产物、事件顺序与 call ID、工具返回失败/抛异常、模型异常脱敏、无效最终答案、步数/超时用量统计、关闭记录，以及临时工作区清理后仍可读取证据。

测试使用 tmp_path、Fake 模型和工具，不读取真实密钥，也不调用真实 API。`fake-secret-for-test` 是专门用来验证脱敏的假值。

新增 summary 约定：原有状态/计数字段之外包含 `estimated_cost_cny`、`elapsed_seconds`、`error`、`final_answer_path`；成功时 error=null，失败时路径=null。

假时钟应允许任意次读取。旧循环测试的时钟只有两个值，本次改为耗尽后保持在第 2 秒，避免增加 Trace 计时就触发 StopIteration。

## 5. 实现顺序与自查

按顺序执行（在项目根目录）：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/agent/test_trace.py -q -x
.\.venv\Scripts\python.exe -m pytest tests/agent/test_loop_trace.py -q -x
.\.venv\Scripts\python.exe -m pytest tests/agent/test_loop.py -q
.\.venv\Scripts\python.exe -m pytest -q
```

先完成两种写入函数，再修关闭记录和事件名，再将最终保存统一放入 `_finish`，最后处理失败事件配对、用量和脱敏。
这些测试当前允许失败；修实现后再逐个变绿，不用修改断言来迁就错误行为。

理解检查：能够解释为什么 messages.json 覆盖而 trace.jsonl 追加；为什么任务副本由运行器保存；为什么收到超时响应也要记账；为什么 `_finish` 要覆盖成功、失败和限制三种终态。

完成后人工阅读一次 Fake 成功轨迹和一次失败轨迹，确认仅靠文件能说明发生了什么。长输出与多工具停止的补缺仍需跟进 [待补缺清单](./BACKLOG.md)，随后再进入步骤 7 的真实模型适配。
