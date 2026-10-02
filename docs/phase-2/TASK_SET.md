# 第二期题集 v1：六条回归题 + 两条跨文件工单题

> 更新：本文记录前八题的设计。现已另增 Flask 本体 009/010，共 10 题；`--all` 将执行十题，且需要独立 Flask 测试环境。最新范围见 [较大仓库说明](./LARGER_REPO.md)。下文八题数量和较大仓库待选择的内容为 v1 历史记录。

新增题已落地、完成本地可复现性验证；尚未进行真实模型评测。

## 1. 范围与来源

沿用已有 Flaskr 3.1.3 干净仓库，在每道新题的临时副本中附加原创工单模块。原仓库、旧六题、依赖和 Agent Loop 均不修改。不是从真实 GitHub issue 提取的问题，也不是 SWE-bench 题。

这是对先前“筛选参数漏传/缓存未失效”两个候选的最小可运行实现。跨文件复杂度增加，但仓库仍小；不能据这两题直接宣布较大仓库检索收益已验证。后续仍需独立选择较大样例。

`workspace_additions` 复制 API、service、repository、cache、模块说明和接口测试，然后 mutation.patch 只修改 service 一行。测试 fixture 注册 blueprint、初始化独立 SQLite ticket 表，每次测试使用独立 Flask app。缓存是每应用的内存字典，不需 Redis、网络、时钟等待或硬件。

007/008 分别保存相同的干净模块快照，避免跨任务源路径和共享文件改变导致实验漂移。今后修改夹具需同步两份并重新跑预检；不能在一次评测中悄悄改变快照。

## 2. 任务及成功条件

| ID | 用户可见症状 | 严格验收关注 |
| --- | --- | --- |
| flaskr-filter-007 | 合法状态筛选仍返回全部工单；无筛选与非法参数正常 | 接口→service→repository 的参数链；最小传参修复；open/closed/不筛选/非法值四条路径 |
| flaskr-cache-008 | 先读再更新后 GET 仍旧值；数据库已更新；冷缓存正常 | 缓存读写与失效键关联；不是缺少 commit；保留缓存、精确失效；预热→更新→DB重读→GET，并检查其他缓存 |

参考根因、补丁和逐条硬要求在各自 oracle.yaml。面向解题模型的 task.yaml 不包含缺陷位置或参考修复；模块 README 只说明正常业务契约。模型可见接口回归测试，这是当前开放测试任务设定，不宣称有隐藏测试泛化能力。

每题包含 task.yaml、oracle.yaml、mutation.patch、assets/。只有 workspace_additions 明确列出的文件被复制给 Agent；Oracle、注入补丁和本说明不进入目标工作区。源码索引只扫描该工作区。

## 3. 本地预检与运行

在项目根目录运行，不调用模型：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_ticket_task_fixtures.py -q
.\.venv\Scripts\python.exe -X utf8 -m scripts.verify_ticket_tasks
```

验证顺序：带模块的干净副本全套 31 passed → 注入后指定回归失败 → 反向应用参考补丁后全套 31 passed。自动化测试还会检查真实 search_symbols 返回注入后的代码，而不是干净实现。

之后需要真实评测时，可用现有入口（以下会收费，本次未执行）：

```powershell
.\.venv\Scripts\python.exe -m scripts.run_agent --task flaskr-filter-007 --repetitions 1
.\.venv\Scripts\python.exe -m scripts.run_agent --task flaskr-cache-008 --repetitions 1
```

`--all --repetitions 1` 现在会执行 8 题，而不是 6 题；未指定 repetitions 时仍沿用配置默认值。干净基线的首次预检仍显示 24 passed；新增 7 个工单测试只存在于 007/008 工作区，二者修复后的全套是 31 passed，不与 ordering 的 25 passed 混淆。

## 4. 评测纪律与未完成项

- 007 用作检索策略开发题；008 预留为策略冻结后的首次对照题。目前只运行确定性测试，未向解题模型发送两题内容。题目作者已知参考答案，不称为独立外部盲测。
- 若用 008 的模型结果调参，将它转为回归集，另留新题。
- 不要求模型必须使用 search_symbols。旧四工具方案和新方案在相同任务/预算下比较；当前 runner 尚无旧/新检索切换开关，完整 A/B 需补这一项，不能将现在的八题运行直接当 A/B。
- evidence_targets 是评估端的关键文件/函数行范围。后续按真正传入模型的工具片段审核关键证据，不以索引中存在或只显示文件名计命中；本次尚未实现自动证据评分器。
- 新题严格通过仍要求总分至少 8 且全部硬要求成立。正常终止、根因正确、验证完整性分别统计。
- 当前 Agent 的 task.json 快照未包含 workspace_additions；复现需保留完整任务目录及版本，不能只复制 task.json。配置与任务内容哈希快照属于后续评测完善项。
- 008 的内存缓存没有并发、多进程或 TTL 语义；不要把这些未要求的能力当成该题根因。

下一步先完成检索片段排序修复和对照开关，再用 007 试跑；策略固定后才跑 008。
