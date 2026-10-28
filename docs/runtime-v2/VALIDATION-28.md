# 增量28：M4 并行实现与内部整合

日期：2026-10-28。基线：dev/3fc250b；初始实现b685c69，独立复审修复2968b2e、7e9014c、f75c068。对应原始规格第7节及A07／A09；实现范围只到本地并行、独立worktree、槽位、串行整合和组合检查，不包含M5双审、AB复核或真实平台效果验收。

## 本轮交付

| 能力 | 实现 |
| --- | --- |
| 可并行计划 | `PlanReport.split=parallel`；至少两个互不依赖的子任务，程序重新拓扑排序；无并行需求仍走`none`／`sequential` |
| 全局实现槽位 | `orchestration/slots.py` 在`jobs.sqlite3`保存跨运行租约，默认上限2；并行worker、无计划、顺序子任务、返工和整合修复都申请同一槽位；等待超时以`limit_reached`停止，多个Issue不能绕过 |
| 独立worktree | 每个并行子任务在运行目录worktree中执行，从父整合工作区当前HEAD创建；worker只返回报告和提交，不修改父状态 |
| 共享额度 | `RunControl.fork`让worker共享模型／工具／Token／费用计数与检查记录，进度、阶段、已读位置线程局部；费用准入计入未结算预留，父检查点读取共享计数 |
| 局部暂停 | 澄清或整合冲突只阻塞受影响子任务及其后继；独立子任务继续完成并整合，之后汇总打开一次人工请求 |
| 整合恢复 | 应用前持久化`pending_integration`；冲突修复异常或中断保留现场和来源提交，显式`recover`后继续同一整合；已提交的整合补记而不重复 |
| 硬退出计数 | worker工具／编辑事件逐条落盘，恢复时从`events.jsonl`重建被遗漏的共享工具／编辑计数，预算账本恢复模型调用和费用 |
| 依赖基线 | 只有依赖子任务已整合进父HEAD后才派发下游；下游worktree基于该HEAD，不读取其他worker的活跃目录 |
| 串行整合 | 父工作区用`cherry-pick --no-commit`依次应用worker提交，成功后提交整合；记录来源提交、基线、整合提交和worktree |
| 冲突处理 | 普通冲突交给受控整合Agent（`IntegrationReport`）；程序检查未合并索引和冲突标记，仍残留则回滚；接口／业务取舍进入人工请求 |
| 组合检查 | 全部整合后统一anchor、独立重跑全部登记检查、审核和批准；组合回归失败或冲突不会直接交付 |
| 报告／状态 | `plan show`、`status`和HTML报告展示`parallel_runs`、`integrations`、来源／整合提交；审批摘要加入整合记录 |

公开说明见[并行与整合](PARALLEL.md)、[计划](PLANNING.md)和[监听](WATCH.md)。

## 独立复审修复

2026-10-28独立复审提出R1—R5，均在M4范围内修复：

| 项 | 修复 |
| --- | --- |
| R1全局槽位被绕过 | 新增`PhaseRunner._implementation_slot`，无计划、顺序子任务、返工和整合修复与并行worker共用同一租约；无计划／顺序路径在槽位占满时不再调用实现模型 |
| R2局部澄清全停父任务 | 调度器不再因单个澄清退出；阻塞任务登记到`parallel_blocked`，独立任务继续提交／整合，无可继续任务后汇总人工请求 |
| R3整合resolver异常残留冲突 | `pending_integration`记录应用前状态；resolver异常保留冲突现场和来源提交，`recover`后从同一整合继续；正常人工分支仍回滚到干净HEAD |
| R4并发费用越过运行上限 | `Quota.pending_cny`把已预留未结算金额计入准入；结算时先结算账本再原子转成accounted，避免释放与入账之间的窗口 |
| R5硬退出丢失工具／编辑计数 | `RunSession._restore_worker_events`从逐条`events.jsonl`重建工具调用和编辑尝试，并按最大值与既有检查点合并 |
| R5补充失败编辑被清零 | 编辑工具先`take`额度再执行；恢复按`tool_started`计数并排除明确的`edit_limit_reached`，失败编辑（如`old_string_not_found`）仍计入 |
| R6干净应用中断后重跑worker | 恢复检查`pending_integration`和暂存区：已应用未提交直接完成整合；未应用则用来源提交重新应用；只有无来源可接续才重跑受影响worker |

修复后原独立探针6项全部通过；补充审复审2项（失败编辑硬退出、干净应用中断恢复）也全部通过。新增产品回归覆盖R1—R6。探针源码与输出见`.local/evidence/m4-independent-review/`和`.local/evidence/m4-independent-rereview/`，不提交到公开仓库。

## 验证

模型与worker为确定性替身，Git／SQLite／运行目录为真实本地组件；没有真实模型并发、真实评论或真实草稿PR写入。

| 检查 | 结果 |
| --- | --- |
| M4专项 | 18项通过（并行、依赖、冲突、局部暂停、槽位、费用预留、硬退出计数、失败编辑计数、异常整合恢复、恢复后转人工、干净应用中断恢复、组合批准等） |
| 完整回归 | **671通过／16跳过，553.89秒** |

命令：

```powershell
$t = (Resolve-Path .local).Path + '\tmp\m4-final4-system-temp'
$env:TEMP=$t; $env:TMP=$t; $env:TMPDIR=$t; $env:GIT_CEILING_DIRECTORIES=$t
python -m pytest tests -q -p no:cacheprovider --basetemp .local/tmp/m4-fix-final4 --tb=short
```

测试入口：[并行与整合](../../tests/runtime/test_parallel.py)。本地探针另验证了：两个独立worker都从同一父基线启动；依赖第三任务看到第一任务整合后的文件；冲突在父工作区产生`UU`，受控修复提交后文件保留双方内容；未解决时回滚后父工作区无冲突标记；槽位占满时无计划实现被拒绝；局部澄清时独立任务继续整合；resolver额度异常后recover可继续；干净应用中断后不重跑worker；并发费用不越过运行上限；硬退出后工具／编辑计数（含失败编辑）可恢复。

## 边界与限制

- 没有真实模型并行调用；线程并发、模型对象共享和两侧工具并发只做了确定性替身验证，不能据此宣称真实并发收益或稳定性。
- 没有Docker后端的并行子任务验证；容器会话与线程组合待测。
- 没有真实GitHub／GitLab评论、子Issue或草稿PR在并行计划下的端到端验证；仍只有一个父汇总草稿PR，不自动合并或关闭工单。
- 暂停时保留worktree／记录和已完成的独立整合，但不自动续用未提交的worker编辑；答复触发M3重规划后按新计划执行。
- 冲突修复依赖模型判断；程序只能检查索引／冲突标记、提交是否产生和最终登记检查，不能保证语义上完全保留双方功能，仍需审核和人工批准。
- 线程共享Python进程，不是安全沙箱；槽位租约按TTL清理，进程崩溃后可能短暂占用。
- 没有和顺序执行做费用／成功率对照。

公开验证记录见本文件；M5双审和M6真实链路仍未开始。
