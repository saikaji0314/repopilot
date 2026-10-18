# 代码检索与上下文工程

检索模块为 Agent 提供带路径和行号的代码证据，重点是有限预算下的定义定位、片段选择与读取效率。系统保留字面搜索，并增加 Python AST 符号索引，通过工具开关比较两种完整检索方案。

## 索引与查询

运行器先创建任务副本并注入缺陷，再对该副本构建内存索引。索引扫描允许范围，解析 Python AST 提取函数和类位置，不导入或执行被分析模块。Oracle、参考补丁及运行产物不属于被分析代码的索引范围。

查询按符号名称精确匹配、部分匹配与文件路径匹配排序，合并结果时保留精确匹配优先级。上下文构建器去重、选择代码片段并限制字符预算，返回来源供模型引用。

| 模块 | 职责 |
| --- | --- |
| [schema.py](../../repopilot/legacy/retrieval/schema.py) | 文件、符号与命中模型 |
| [index.py](../../repopilot/legacy/retrieval/index.py) | 路径检查、源码扫描与 AST 提取 |
| [search.py](../../repopilot/legacy/retrieval/search.py) | 名称与路径排序 |
| [context.py](../../repopilot/legacy/retrieval/context.py) | 去重、片段选择与字符预算 |
| [工具包装](../../repopilot/legacy/tools/base.py) | 将索引查询接入统一 ToolResult |

`search_symbols` 适合已知函数或类名的定义定位；`search_code` 适合变量、配置键、表达式和调用位置。当前采用确定性的轻量索引，未引入 Embedding 或向量数据库；跨文件调用链仍需模型结合搜索与文件读取调查。

## 能力接入与测试

`--retrieval-mode code` 使用四个基础工具并跳过建索引；`symbols` 增加符号工具及相应描述。实际工具列表与可见任务权限同步，运行快照记录工具定义和索引耗时。

算法测试覆盖索引、排序与上下文选择；Fake 串联测试验证模型工具调用、注册表、观察回传和最终答案的完整链路；真实冒烟检查协议接入。题集回归测试核对新增文件在缺陷注入后被索引，防止读到干净版本。

代码入口：[检索测试](../../tests/retrieval) · [串联测试](../../tests/agent/test_search_symbols_integration.py) · [工单题集测试](../../tests/test_ticket_task_fixtures.py)。

## 固定任务对照

题集包含六条初始 Flaskr 任务、两条跨文件工单任务以及两条官方 Flask 源码任务。对照脚本为每题交替执行四工具/五工具方案，固定模型、预算和任务标准，保存源码哈希、配置与指标。Agent 看不到 Oracle，严格评分由评测者核对。

| 指标 | 四工具 | 五工具 |
| --- | ---: | ---: |
| 严格诊断通过 | 5/10 | 4/10 |
| 合法最终答案 | 8/10 | 8/10 |
| 累计 Token | 381398 | 472451 |
| 估算费用 | ¥0.984730 | ¥1.202716 |
| 平均 Loop 耗时 | 69.56 秒 | 80.62 秒 |
| 证据范围命中，仅扩展四题 | 13/15 | 12/15 |
| 自然调用符号工具的任务 | 不提供 | 2/10 |

本次初筛未证明符号工具带来端到端收益。证据范围命中仅表示至少一行进入工具观察，不能替代完整调用链理解；每题每组一次也不足以判定统计显著性。失败复盘将输出协议、过度调查与验证遗漏分别记录，避免把所有失败归结为检索问题。

完整逐题结果和评分边界见 [对照报告](../../evals/reports/retrieval-comparison-v1.md)。

## 复现

```powershell
.\.venv\Scripts\python.exe -m scripts.run_agent --task flaskr-filter-007 --repetitions 1 --retrieval-mode symbols
```

环境准备见 [项目首页](../../README.md)。四工具/五工具完整对照使用 `python -m scripts.compare_retrieval`，会调用真实模型；运行全题前需安装 Flask 源码独立测试环境。指标采集与评分步骤见 [对照实验说明](EVALUATION.md)。

后续改进集中在索引警告与截断信息、上下文复用、证据覆盖及收尾策略。详细设计与限制见 [实现记录](IMPLEMENTATION_PLAN.md)、[题集设计](TASK_SET.md)、[较大仓库说明](LARGER_REPO.md) 和 [限制清单](BACKLOG.md)。
