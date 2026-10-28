# 增量28：M4 并行实现与内部整合

日期：2026-10-28。基线：dev/3fc250b。对应原始规格第7节及A07／A09；实现范围只到本地并行、独立worktree、槽位、串行整合和组合检查，不包含M5双审、AB复核或真实平台效果验收。

## 本轮交付

| 能力 | 实现 |
| --- | --- |
| 可并行计划 | `PlanReport.split=parallel`；至少两个互不依赖的子任务，程序重新拓扑排序；无并行需求仍走`none`／`sequential` |
| 全局实现槽位 | `orchestration/slots.py` 在`jobs.sqlite3`保存跨运行租约，默认上限2；等待超时以`limit_reached`停止，多个Issue不能绕过 |
| 独立worktree | 每个并行子任务在运行目录worktree中执行，从父整合工作区当前HEAD创建；worker只返回报告和提交，不修改父状态 |
| 共享额度 | `RunControl.fork`让worker共享模型／工具／Token／费用计数与检查记录，进度、阶段、已读位置线程局部；父检查点读取共享计数 |
| 依赖基线 | 只有依赖子任务已整合进父HEAD后才派发下游；下游worktree基于该HEAD，不读取其他worker的活跃目录 |
| 串行整合 | 父工作区用`cherry-pick --no-commit`依次应用worker提交，成功后提交整合；记录来源提交、基线、整合提交和worktree |
| 冲突处理 | 普通冲突交给受控整合Agent（`IntegrationReport`）；程序检查未合并索引和冲突标记，仍残留则回滚；接口／业务取舍进入人工请求 |
| 组合检查 | 全部整合后统一anchor、独立重跑全部登记检查、审核和批准；组合回归失败或冲突不会直接交付 |
| 报告／状态 | `plan show`、`status`和HTML报告展示`parallel_runs`、`integrations`、来源／整合提交；审批摘要加入整合记录 |

公开说明见[并行与整合](PARALLEL.md)、[计划](PLANNING.md)和[监听](WATCH.md)。

## 验证

模型与worker为确定性替身，Git／SQLite／运行目录为真实本地组件；没有真实模型并发、真实评论或真实草稿PR写入。

| 检查 | 结果 |
| --- | --- |
| M4专项 | 11项通过（两个独立任务同时启动、依赖等待已整合前置、普通冲突受控修复、冲突转人工、单槽位串行、槽位租约、worker提交、fork共享额度、报告展示、暂停、组合候选批准） |
| 完整回归 | **664通过／16跳过，552.37秒** |

命令：

```powershell
$t = (Resolve-Path .local).Path + '\tmp\m4-system-temp'
$env:TEMP=$t; $env:TMP=$t; $env:TMPDIR=$t; $env:GIT_CEILING_DIRECTORIES=$t
python -m pytest tests -q -p no:cacheprovider --basetemp .local/tmp/m4-final --tb=short
```

测试入口：[并行与整合](../../tests/runtime/test_parallel.py)。本地探针另验证了：两个独立worker都从同一父基线启动；依赖第三任务看到第一任务整合后的文件；冲突在父工作区产生`UU`，受控修复提交后文件保留双方内容；未解决时回滚后父工作区无冲突标记。

## 边界与限制

- 没有真实模型并行调用；线程并发、模型对象共享和两侧工具并发只做了确定性替身验证，不能据此宣称真实并发收益或稳定性。
- 没有Docker后端的并行子任务验证；容器会话与线程组合待测。
- 没有真实GitHub／GitLab评论、子Issue或草稿PR在并行计划下的端到端验证；仍只有一个父汇总草稿PR，不自动合并或关闭工单。
- 暂停时停止派发并保留worktree／记录，但不自动续用未提交的worker编辑；答复触发M3重规划后按新计划执行。
- 冲突修复依赖模型判断；程序只能检查索引／冲突标记、提交是否产生和最终登记检查，不能保证语义上完全保留双方功能，仍需审核和人工批准。
- 线程共享Python进程，不是安全沙箱；槽位租约按TTL清理，进程崩溃后可能短暂占用。
- 没有和顺序执行做费用／成功率对照。

公开验证记录见本文件；M5双审和M6真实链路仍未开始。
