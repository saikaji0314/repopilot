# 新版独立修复评分：两个公开开发题

本目录验证评分流程，不是留出题集，也不是模型修复成绩。夹具与控制补丁由项目开发者编写，尚未进行付费模型解题。旧版诊断评分不参与这里的判定。

| 题目 | 需求 | 固定回归与额外验收 |
| --- | --- | --- |
| `python-limit` | 已按新到旧排列的记录，正确取前N条并校验limit | 保留不传limit行为；检查1／2／超出长度、空输入、不修改输入和非法参数 |
| `javascript-slug` | ASCII slug处理多组分隔符并去除首尾分隔符 | 保留Hello World例子；检查多组空格、符号、纯符号、空串及数字 |

每题的 `baseline/` 是候选起点，`case.json` 固定字节摘要、可编辑源码和可信检查，`oracle/` 保存工作区之外的固定回归／独立验收。`patches/correct.patch` 与 `bad.patch` 是手写控制样本：坏补丁仍能通过固定回归，但不能通过完整需求验收。

## 运行

从仓库根目录执行，需要 Git、当前Python环境；JavaScript题另需PATH中的Node.js。

```powershell
# 未修复基线：预期unsolved，退出码1
.\.venv\Scripts\python.exe -m repopilot.evaluation python-limit
# 正确补丁：预期solved，退出码0
.\.venv\Scripts\python.exe -m repopilot.evaluation python-limit --patch evals/repair/tasks/python-limit/patches/correct.patch
# 坏补丁：预期unsolved，退出码1
.\.venv\Scripts\python.exe -m repopilot.evaluation python-limit --patch evals/repair/tasks/python-limit/patches/bad.patch
# JavaScript相同流程
.\.venv\Scripts\python.exe -m repopilot.evaluation javascript-slug --patch evals/repair/tasks/javascript-slug/patches/correct.patch
```

工具不加载模型或GitHub凭证，不调用模型；每次报告保存在 `.local/repair-evals/<随机ID>/report.json`。`--output` 可指定本地输出目录；其 `_work/` 下只创建并清理本次临时工作区，不修改原始夹具或累计模型账本。当前从源码checkout运行，尚未支持独立分发题集。

## 评分规则

1. 核对基线摘要，从基线复制新工作区并创建本地临时Git索引，避免向上误发现主仓库。
2. 检查环境：记录解释器、Git与平台版本；固定回归必须通过，额外需求验收必须以明确的断言失败协议结束，证明基线确实未满足需求。
3. 冻结补丁内容与摘要，只接受当前题登记的文本源码修改；改公开测试、验收文件、其他路径、二进制或无效补丁均拒绝。首版不允许新增或删除任务外文件。
4. 对候选再次执行固定回归和独立验收。退出码和末行 `{"repair_check":"passed/failed"}` 协议必须对应，崩溃、导入失败、缺失解释器、超时不能当作正确的需求断言失败。
5. 全部检查通过才是 `solved`；评分前后核对候选内容，检查不能偷偷改候选再报告通过。报告保存基线、任务、验收、补丁摘要和真实输出，不接收Agent的自报结论作为分数。

| 状态 | 含义 |
| --- | --- |
| `solved` | 独立回归和需求验收全部通过 |
| `unsolved` | 环境正常，仍有真实需求或回归断言失败 |
| `invalid_patch` | 补丁格式／路径／体积不符合题目约束，或检查改动候选 |
| `environment_error` | 基线失效、预检不满足、解释器／评分脚本异常等，不能计算为成功 |
| `evaluation_error` | 基线预检正常，但候选运行／评分未产生有效判定；可能是候选语法错误，不能直接归咎环境 |
| `timeout` | 候选检查超时，单独列出 |

`case.json` 和控制补丁使用LF；根 `.gitattributes` 固定此目录的检出换行，防止Windows自动换行改变基线摘要。

## 边界与后续

验收对开发者公开。“独立”指不从候选读取可改的测试、也不采信Agent报告；它不代表验收内容对同一宿主上的恶意代码保密。仓库源码仍以当前用户权限执行，可以产生宿主副作用。这里仅运行受信的小型开发夹具，尚无OS沙箱、隐藏答案隔离或恶意补丁防御的完整保证。

生成侧今后只应接收需求、`baseline/` 与公开检查；`RepairCase.scoring_task` 包含外部验收路径，仅供评分器使用，不能直接交给解题Agent。本增量尚未接入自动解题、批次预算、完整12题或留出协议；下一步先扩开发案例、再冻结真正独立的留出评分环境。真实模型效果必须另跑并单独记费用。
