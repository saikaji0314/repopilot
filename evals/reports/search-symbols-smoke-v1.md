# search_symbols 接入冒烟记录

用户授权新增 Fake 串联测试，并运行一条显式要求使用新工具的真实冒烟。未修改正式任务、Oracle、系统提示词或 Loop。

## 本地检查

新增 `tests/agent/test_search_symbols_integration.py`：真实临时 Python 文件 → build_index → SearchSymbolsTool/ToolRegistry → FakeChatModel/agent_loop → 工具结果进入下一轮消息 → Trace/messages/final_answer 落盘。

用两个检索调用检查复用同一工具持有的索引、schema 传递、call ID 配对、正确路径/代码内容以及结果成功。它验证接入链路，不验证模型诊断能力，也不替代 runner 索引生命周期专项测试。

- 单独运行新增测试：1 passed。
- 全量 `python -X utf8 -m pytest -q`：170 passed、2 skipped。跳过不算验证通过。

## 真实冒烟

脚本：`python -X utf8 -m scripts.smoke_search_symbols`。这是付费命令，不属于默认单元测试。

只执行 flaskr-login-002 一次，在用户消息末尾附加明确指令：先调用 search_symbols，query=login、limit=3，再继续原任务。沿用现有模型、步数/次数/费用/时间限制，网络自动重试关闭，Loop 自身的有限格式恢复不变。索引只建立在已注入缺陷的临时工作区。

首次尝试 development-run-04（原始产物未随仓库分发，标识：`../runs/smoke/development-run-04/flaskr-login-002/run-01/`） 遇到 Connection error，未收到模型响应；记录 usage=0、估算费用=0，不据此断言服务端账单绝对为零。网络权限获准后重试，失败产物保留。

成功批次：development-run-05（原始产物未随仓库分发，标识：`../runs/smoke/development-run-05/flaskr-login-002/run-01/`）。

| 检查 | 结果 |
| --- | --- |
| 第一次工具调用 | search_symbols，query=login、limit=3 |
| 工具结果 | ok=true，有带路径/行号的实际源码；truncated=true |
| 最终运行 | completed / final_answer |
| 模型步骤 / 工具次数 | 8 / 10 |
| 最终格式恢复 | 1 次，恢复成功 |
| Token | 42114（输入 36762，输出 5352） |
| 估算费用 | ¥0.116340，不是实付账单 |
| Loop 耗时 | 99.484 秒，不含环境预检和索引构建 |

该目录另外保存 `tools.json`（实际提供的工具定义）和 `smoke.json`（显式引导标记、指令、非敏感配置）。与正式 Agent 评测目录分开，不计自主工具选择成功率，不宣称检索效率提升。

## 得出的结论

新工具可以被真实模型调用，返回值可用于后续调查，最终也能正常作答。此前未调用不等于接入失败。

暴露的优化点：merge_overlapping_hits 按文件/行号排序后，把部分命中的 login_required 放在精确命中的 login 前面；1000 字符预算在 login 第 98 行截断。模型随后用 read_file 补读。下一步优先保留检索优先级，再观察片段预算，而不是仅强制所有任务调用工具或直接增大预算。

本次只是工具接入冒烟，不对最终答案做新一轮严格 Oracle 评分，不证明新方案优于旧方案。
